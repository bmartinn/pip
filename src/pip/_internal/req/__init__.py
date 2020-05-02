# The following comment should be removed at some point in the future.
# mypy: strict-optional=False

from __future__ import absolute_import

import logging
from multiprocessing.pool import ThreadPool

from pip._internal.utils.logging import indent_log
from pip._internal.utils.typing import MYPY_CHECK_RUNNING

from .req_file import parse_requirements
from .req_install import InstallRequirement
from .req_set import RequirementSet

if MYPY_CHECK_RUNNING:
    from typing import Any, List, Sequence

__all__ = [
    "RequirementSet", "InstallRequirement",
    "parse_requirements", "install_given_reqs",
]

logger = logging.getLogger(__name__)


class InstallationResult(object):
    def __init__(self, name):
        # type: (str) -> None
        self.name = name

    def __repr__(self):
        # type: () -> str
        return "InstallationResult(name={!r})".format(self.name)


def install_given_reqs(
    to_install,  # type: List[InstallRequirement]
    install_options,  # type: List[str]
    global_options=(),  # type: Sequence[str]
    multi_thread=False,  # type: bool
    *args,  # type: Any
    **kwargs  # type: Any
):
    # type: (...) -> List[InstallationResult]
    """
    Install everything in the given list.

    (to be called after having downloaded and unpacked the packages)
    """

    def _single_install(a_installed, index, a_requirement, allow_raise=False):
        if a_requirement.should_reinstall:
            logger.info('Attempting uninstall: %s', a_requirement.name)
            with indent_log():
                uninstalled_pathset = a_requirement.uninstall(
                    auto_confirm=True
                )
        try:
            a_requirement.install(
                install_options,
                global_options,
                *args,
                **kwargs
            )
        except Exception:
            should_rollback = (
                    a_requirement.should_reinstall and
                    not a_requirement.install_succeeded
            )
            # if install did not succeed, rollback previous uninstall
            if should_rollback:
                uninstalled_pathset.rollback()
            if allow_raise:
                raise
        else:
            should_commit = (
                    a_requirement.should_reinstall and
                    a_requirement.install_succeeded
            )
            if should_commit:
                uninstalled_pathset.commit()
            a_installed[index] = InstallationResult(a_requirement.name)

    if to_install:
        logger.info(
            'Installing collected packages: %s',
            ', '.join([req.name for req in to_install]),
        )

    # pre allocate installed package names
    installed = [None] * len(to_install)

    if multi_thread:
        # first let's try to install in parallel, if we fail we do it by order.
        pool = ThreadPool()
        pool.starmap(_single_install, [(installed, i, r) for i, r in enumerate(to_install)])
        pool.close()
        pool.join()

    with indent_log():
        for i, requirement in enumerate(to_install):
            if installed[i] is None:
                _single_install(installed, i, requirement, allow_raise=True)

    return [i for i in installed if i is not None]
