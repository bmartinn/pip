from __future__ import division

import itertools
import sys
from logging import Logger
from signal import SIGINT, default_int_handler, signal

from pip._vendor import six
from pip._vendor.progress.bar import Bar, FillingCirclesBar, IncrementalBar
from pip._vendor.progress.spinner import Spinner

from pip._internal.utils.compat import WINDOWS
from pip._internal.utils.logging import get_indentation
from pip._internal.utils.misc import format_size
from pip._internal.utils.typing import MYPY_CHECK_RUNNING

try:
    from threading import RLock, Lock, current_thread, _MainThread
except ImportError:  # Platform-specific: No threads available
    class Lock:
        def __enter__(self):
            pass

        def __exit__(self, exc_type, exc_value, traceback):
            pass

        def locked(self):
            return False

        def acquire(self, *_, **__):
            return False

        def release(self, *_, **__):
            return False

    RLock = Lock
    current_thread = _MainThread = None

if MYPY_CHECK_RUNNING:
    from typing import Any, Dict, List

try:
    from pip._vendor import colorama
# Lots of different errors can come from this, including SystemError and
# ImportError.
except Exception:
    colorama = None


def _select_progress_class(preferred, fallback):
    # type: (Bar, Bar) -> Bar
    encoding = getattr(preferred.file, "encoding", None)

    # If we don't know what encoding this file is in, then we'll just assume
    # that it doesn't support unicode and use the ASCII bar.
    if not encoding:
        return fallback

    # Collect all of the possible characters we want to use with the preferred
    # bar.
    characters = [
        getattr(preferred, "empty_fill", six.text_type()),
        getattr(preferred, "fill", six.text_type()),
    ]
    characters += list(getattr(preferred, "phases", []))

    # Try to decode the characters we're using for the bar using the encoding
    # of the given file, if this works then we'll assume that we can use the
    # fancier bar and if not we'll fall back to the plaintext bar.
    try:
        six.text_type().join(characters).encode(encoding)
    except UnicodeEncodeError:
        return fallback
    else:
        return preferred


_BaseBar = _select_progress_class(IncrementalBar, Bar)  # type: Any


class InterruptibleMixin(object):
    """
    Helper to ensure that self.finish() gets called on keyboard interrupt.

    This allows downloads to be interrupted without leaving temporary state
    (like hidden cursors) behind.

    This class is similar to the progress library's existing SigIntMixin
    helper, but as of version 1.2, that helper has the following problems:

    1. It calls sys.exit().
    2. It discards the existing SIGINT handler completely.
    3. It leaves its own handler in place even after an uninterrupted finish,
       which will have unexpected delayed effects if the user triggers an
       unrelated keyboard interrupt some time after a progress-displaying
       download has already completed, for example.
    """

    def __init__(self, *args, **kwargs):
        # type: (List[Any], Dict[Any, Any]) -> None
        """
        Save the original SIGINT handler for later.
        """
        super(InterruptibleMixin, self).__init__(  # type: ignore
            *args,
            **kwargs
        )
        self._finished = False

        if current_thread is not None and not isinstance(current_thread(), _MainThread):
            self.original_handler = None
            return

        self.original_handler = signal(SIGINT, self.handle_sigint)

        # If signal() returns None, the previous handler was not installed from
        # Python, and we cannot restore it. This probably should not happen,
        # but if it does, we must restore something sensible instead, at least.
        # The least bad option should be Python's default SIGINT handler, which
        # just raises KeyboardInterrupt.
        if self.original_handler is None:
            self.original_handler = default_int_handler

    def finish(self):
        # type: () -> None
        """
        Restore the original SIGINT handler after finishing.

        This should happen regardless of whether the progress display finishes
        normally, or gets interrupted.
        """
        self._finished = True
        super(InterruptibleMixin, self).finish()  # type: ignore
        if self.original_handler:
            signal(SIGINT, self.original_handler)
            self.original_handler = None

    def handle_sigint(self, signum, frame):  # type: ignore
        """
        Call self.finish() before delegating to the original SIGINT handler.

        This handler should only be in place while the progress display is
        active.
        """
        original_handler = self.original_handler
        self.finish()
        if original_handler:
            original_handler(signum, frame)

    def __del__(self):
        # if we haven't called finish yet, we should
        if not self._finished:
            super(InterruptibleMixin, self).finish()


class SilentBar(Bar):

    def update(self):
        # type: () -> None
        pass


class BlueEmojiBar(IncrementalBar):

    suffix = "%(percent)d%%"
    bar_prefix = " "
    bar_suffix = " "
    phases = (u"\U0001F539", u"\U0001F537", u"\U0001F535")  # type: Any


class DownloadProgressMixin(object):

    def __init__(self, *args, **kwargs):
        # type: (List[Any], Dict[Any, Any]) -> None
        super(DownloadProgressMixin, self).__init__(  # type: ignore
            *args,
            **kwargs
        )
        self.message = (" " * (
            get_indentation() + 2
        )) + self.message  # type: str

    @property
    def downloaded(self):
        # type: () -> str
        return format_size(self.index)  # type: ignore

    @property
    def download_speed(self):
        # type: () -> str
        # Avoid zero division errors...
        if self.avg == 0.0:  # type: ignore
            return "..."
        return format_size(1 / self.avg) + "/s"  # type: ignore

    @property
    def pretty_eta(self):
        # type: () -> str
        if self.eta:  # type: ignore
            return "eta {}".format(self.eta_td)  # type: ignore
        return ""

    def iter(self, it):  # type: ignore
        for x in it:
            yield x
            self.next(len(x))
        self.finish()


class WindowsMixin(object):

    def __init__(self, *args, **kwargs):
        # type: (List[Any], Dict[Any, Any]) -> None
        # The Windows terminal does not support the hide/show cursor ANSI codes
        # even with colorama. So we'll ensure that hide_cursor is False on
        # Windows.
        # This call needs to go before the super() call, so that hide_cursor
        # is set in time. The base progress bar class writes the "hide cursor"
        # code to the terminal in its init, so if we don't set this soon
        # enough, we get a "hide" with no corresponding "show"...
        if WINDOWS and self.hide_cursor:  # type: ignore
            self.hide_cursor = False

        super(WindowsMixin, self).__init__(*args, **kwargs)  # type: ignore

        # Check if we are running on Windows and we have the colorama module,
        # if we do then wrap our file with it.
        if WINDOWS and colorama:
            self.file = colorama.AnsiToWin32(self.file)  # type: ignore
            # The progress code expects to be able to call self.file.isatty()
            # but the colorama.AnsiToWin32() object doesn't have that, so we'll
            # add it.
            self.file.isatty = lambda: self.file.wrapped.isatty()
            # The progress code expects to be able to call self.file.flush()
            # but the colorama.AnsiToWin32() object doesn't have that, so we'll
            # add it.
            self.file.flush = lambda: self.file.wrapped.flush()


class BaseDownloadProgressBar(WindowsMixin, InterruptibleMixin,
                              DownloadProgressMixin):

    file = sys.stdout
    message = "%(percent)d%%"
    suffix = "%(downloaded)s %(download_speed)s %(pretty_eta)s"
    lock = Lock()
    force_progress = False

    def __init__(self, *args, **kwargs):
        super(BaseDownloadProgressBar, self).__init__(*args, **kwargs)
        self._locked = False
        self._force_line = False

    def writeln(self, line):
        if self._force_line:
            return super(BaseDownloadProgressBar, self).writeln(line)

        if not line or not line.strip():
            with LoggerPatch.log_lock:
                return super(BaseDownloadProgressBar, self).writeln(line)

        # try to get the lock
        if self._locked or (self.lock and self.lock.acquire(False)):
            self._locked = True
            with LoggerPatch.log_lock:
                return super(BaseDownloadProgressBar, self).writeln(line)

    def finish(self):
        with LoggerPatch.log_lock:
            if not self._locked and hasattr(self, 'update'):
                self._force_line = True
                self.update()
                self._force_line = False
            ret = super(BaseDownloadProgressBar, self).finish()
        if self._locked:
            if self.lock:
                self.lock.release()
            self._locked = False
        return ret

    def is_tty(self):
        # If force progress bar, act as if this is tty
        if self.force_progress:
            return True
        return super(BaseDownloadProgressBar, self).is_tty()

    def __del__(self):
        if self._locked:
            if self.lock:
                self.lock.release()
            self._locked = False
        super(BaseDownloadProgressBar, self).__del__()


class LoggerPatch:
    log_lock = RLock()

    @staticmethod
    def _safe_log(self, level, msg, args, **kwargs):
        with LoggerPatch.log_lock:
            if msg.strip() and BaseDownloadProgressBar.lock and BaseDownloadProgressBar.lock.locked():
                msg = '\n' + msg
            if level >= 40:
                print('\n')
                BaseDownloadProgressBar.lock = None
            return self._original_log(level, msg, args, **kwargs)

    @staticmethod
    def patch_logger():
        # only patch Logger once
        if not hasattr(Logger, '_original_log'):
            Logger._original_log = Logger._log
            Logger._log = LoggerPatch._safe_log


# NOTE: The "type: ignore" comments on the following classes are there to
#       work around https://github.com/python/typing/issues/241


class DefaultDownloadProgressBar(BaseDownloadProgressBar,
                                 _BaseBar):
    pass


class DownloadSilentBar(BaseDownloadProgressBar, SilentBar):  # type: ignore
    pass


class DownloadBar(BaseDownloadProgressBar,  # type: ignore
                  Bar):
    pass


class DownloadFillingCirclesBar(BaseDownloadProgressBar,  # type: ignore
                                FillingCirclesBar):
    pass


class DownloadBlueEmojiProgressBar(BaseDownloadProgressBar,  # type: ignore
                                   BlueEmojiBar):
    pass


class DownloadProgressSpinner(WindowsMixin, InterruptibleMixin,
                              DownloadProgressMixin, Spinner):

    file = sys.stdout
    suffix = "%(downloaded)s %(download_speed)s"

    def next_phase(self):  # type: ignore
        if not hasattr(self, "_phaser"):
            self._phaser = itertools.cycle(self.phases)
        return next(self._phaser)

    def update(self):
        # type: () -> None
        message = self.message % self
        phase = self.next_phase()
        suffix = self.suffix % self
        line = ''.join([
            message,
            " " if message else "",
            phase,
            " " if suffix else "",
            suffix,
        ])

        self.writeln(line)


BAR_TYPES = {
    "off": (DownloadSilentBar, DownloadSilentBar),
    "on": (DefaultDownloadProgressBar, DownloadProgressSpinner),
    "ascii": (DownloadBar, DownloadProgressSpinner),
    "pretty": (DownloadFillingCirclesBar, DownloadProgressSpinner),
    "emoji": (DownloadBlueEmojiProgressBar, DownloadProgressSpinner)
}


def DownloadProgressProvider(progress_bar, max=None):  # type: ignore
    if max is None or max == 0:
        return BAR_TYPES[progress_bar][1]().iter
    else:
        return BAR_TYPES[progress_bar][0](max=max).iter
