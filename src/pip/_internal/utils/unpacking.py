"""Utilities related archives.
"""

# The following comment should be removed at some point in the future.
# mypy: strict-optional=False
# mypy: disallow-untyped-defs=False

from __future__ import absolute_import

import logging
import os
import shutil
import stat
import tarfile
import zipfile
from glob import glob

from pip._internal.exceptions import InstallationError
from pip._internal.utils.filetypes import (
    BZ2_EXTENSIONS,
    TAR_EXTENSIONS,
    XZ_EXTENSIONS,
    ZIP_EXTENSIONS,
)
from pip._internal.utils.misc import ensure_dir
from pip._internal.utils.typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from typing import Iterable, List, Optional, Text, Union


logger = logging.getLogger(__name__)


SUPPORTED_EXTENSIONS = ZIP_EXTENSIONS + TAR_EXTENSIONS

try:
    import bz2  # noqa
    SUPPORTED_EXTENSIONS += BZ2_EXTENSIONS
except ImportError:
    logger.debug('bz2 module is not available')

try:
    # Only for Python 3.3+
    import lzma  # noqa
    SUPPORTED_EXTENSIONS += XZ_EXTENSIONS
except ImportError:
    logger.debug('lzma module is not available')


def current_umask():
    """Get the current umask which involves having to set it temporarily."""
    mask = os.umask(0)
    os.umask(mask)
    return mask


def split_leading_dir(path):
    # type: (Union[str, Text]) -> List[Union[str, Text]]
    path = path.lstrip('/').lstrip('\\')
    if (
        '/' in path and (
            ('\\' in path and path.find('/') < path.find('\\')) or
            '\\' not in path
        )
    ):
        return path.split('/', 1)
    elif '\\' in path:
        return path.split('\\', 1)
    else:
        return [path, '']


def has_leading_dir(paths):
    # type: (Iterable[Union[str, Text]]) -> bool
    """Returns true if all the paths have the same leading path name
    (i.e., everything is in one subdirectory in an archive)"""
    common_prefix = None
    for path in paths:
        prefix, rest = split_leading_dir(path)
        if not prefix:
            return False
        elif common_prefix is None:
            common_prefix = prefix
        elif prefix != common_prefix:
            return False
    return True


def is_within_directory(directory, target):
    # type: ((Union[str, Text]), (Union[str, Text])) -> bool
    """
    Return true if the absolute path of target is within the directory
    """
    abs_directory = os.path.abspath(directory)
    abs_target = os.path.abspath(target)

    prefix = os.path.commonprefix([abs_directory, abs_target])
    return prefix == abs_directory


class CachedZipFile(zipfile.ZipFile):
    _temp_cached_unpacked_zip = {}

    @classmethod
    def update_temp_cache_folder(cls, zip_file_name, temp_cached_folder):
        cls._temp_cached_unpacked_zip[str(zip_file_name)] = str(temp_cached_folder)

    def __init__(self,  file, mode="r", *args, **kwargs):
        if mode != "r" or not isinstance(file, str) or not self._temp_cached_unpacked_zip.get(os.path.realpath(file)):
            self._initialized = True
            self._tmp_cached_dir = None
            super(CachedZipFile, self).__init__(file, mode, *args, **kwargs)
        else:
            self._args = [file, mode] + list(args)
            self._kwargs = kwargs
            self._initialized = False
            self._name_list = None
            self._tmp_cached_dir = self._temp_cached_unpacked_zip.get(os.path.realpath(file))

    def get_temp_cache_folder(self):
        if self._tmp_cached_dir and os.path.isdir(self._tmp_cached_dir):
            return self._tmp_cached_dir
        return None

    def _init(self):
        if not self._initialized:
            self._initialized = True
            super(CachedZipFile, self).__init__(*self._args, **self._kwargs)

    def _RealGetContents(self):
        self._init()
        return super(CachedZipFile, self)._RealGetContents()

    def namelist(self):
        if not self._initialized and self._tmp_cached_dir and os.path.isdir(self._tmp_cached_dir):
            if self._name_list is None:
                self._name_list = [os.path.relpath(f, self._tmp_cached_dir).replace(os.sep, '/')
                                   for f in glob(os.path.join(self._tmp_cached_dir, '**'), recursive=True)]
                self._name_list = [n for n in self._name_list if n != '.']
            return self._name_list

        self._init()
        return super(CachedZipFile, self).namelist()

    def infolist(self):
        self._init()
        return super(CachedZipFile, self).infolist()

    def printdir(self, file=None):
        self._init()
        return super(CachedZipFile, self).printdir(file)

    def testzip(self):
        self._init()
        return super(CachedZipFile, self).testzip()

    def getinfo(self, name):
        self._init()
        return super(CachedZipFile, self).getinfo(name)

    def setpassword(self, pwd):
        self._init()
        return super(CachedZipFile, self).setpassword(pwd)

    @property
    def comment(self):
        self._init()
        return self._comment

    @comment.setter
    def comment(self, comment):
        self._init()
        super(CachedZipFile, self).comment(comment)

    def read(self, name, pwd=None):
        if not self._initialized and self._tmp_cached_dir and os.path.isdir(self._tmp_cached_dir):
            try:
                filename = os.path.join(self._tmp_cached_dir, name.replace('/', os.sep))
                if os.path.isfile(filename):
                    with open(filename, 'r') as f:
                        return f.read()
            except Exception:
                pass

        self._init()
        return super(CachedZipFile, self).read(name, pwd)

    def open(self, name, *args, **kwargs):
        self._init()
        return super(CachedZipFile, self).open(name, *args, **kwargs)

    def extract(self, member, path=None, pwd=None):
        self._init()
        return super(CachedZipFile, self).extract(member, path, pwd)

    def extractall(self, path=None, members=None, pwd=None):
        self._init()
        return super(CachedZipFile, self).extractall(path, members, pwd)

    def write(self, filename, arcname=None, compress_type=None):
        self._init()
        return super(CachedZipFile, self).write(filename, arcname, compress_type)

    def writestr(self, zinfo_or_arcname, data, compress_type=None):
        self._init()
        return super(CachedZipFile, self).writestr(zinfo_or_arcname, data, compress_type)

    def close(self):
        if not self._initialized:
            return
        self._init()
        return super(CachedZipFile, self).close()


def unzip_file(filename, location, flatten=True):
    # type: (str, str, bool) -> None
    """
    Unzip the file (with path `filename`) to the destination `location`.  All
    files are written based on system defaults and umask (i.e. permissions are
    not preserved), except that regular file members with any execute
    permissions (user, group, or world) have "chmod +x" applied after being
    written. Note that for windows, any execute changes using os.chmod are
    no-ops per the python docs.
    """
    ensure_dir(location)
    zipfp = open(filename, 'rb')
    try:
        zip = zipfile.ZipFile(zipfp, allowZip64=True)
        leading = has_leading_dir(zip.namelist()) and flatten
        for info in zip.infolist():
            name = info.filename
            fn = name
            if leading:
                fn = split_leading_dir(name)[1]
            fn = os.path.join(location, fn)
            dir = os.path.dirname(fn)
            if not is_within_directory(location, fn):
                message = (
                    'The zip file ({}) has a file ({}) trying to install '
                    'outside target directory ({})'
                )
                raise InstallationError(message.format(filename, fn, location))
            if fn.endswith('/') or fn.endswith('\\'):
                # A directory
                ensure_dir(fn)
            else:
                ensure_dir(dir)
                # Don't use read() to avoid allocating an arbitrarily large
                # chunk of memory for the file's content
                fp = zip.open(name)
                try:
                    with open(fn, 'wb') as destfp:
                        shutil.copyfileobj(fp, destfp)
                finally:
                    fp.close()
                    mode = info.external_attr >> 16
                    # if mode and regular file and any execute permissions for
                    # user/group/world?
                    if mode and stat.S_ISREG(mode) and mode & 0o111:
                        # make dest file have execute for user/group/world
                        # (chmod +x) no-op on windows per python docs
                        os.chmod(fn, (0o777 - current_umask() | 0o111))
    finally:
        zipfp.close()

    CachedZipFile.update_temp_cache_folder(filename, location)


def untar_file(filename, location):
    # type: (str, str) -> None
    """
    Untar the file (with path `filename`) to the destination `location`.
    All files are written based on system defaults and umask (i.e. permissions
    are not preserved), except that regular file members with any execute
    permissions (user, group, or world) have "chmod +x" applied after being
    written.  Note that for windows, any execute changes using os.chmod are
    no-ops per the python docs.
    """
    ensure_dir(location)
    if filename.lower().endswith('.gz') or filename.lower().endswith('.tgz'):
        mode = 'r:gz'
    elif filename.lower().endswith(BZ2_EXTENSIONS):
        mode = 'r:bz2'
    elif filename.lower().endswith(XZ_EXTENSIONS):
        mode = 'r:xz'
    elif filename.lower().endswith('.tar'):
        mode = 'r'
    else:
        logger.warning(
            'Cannot determine compression type for file %s', filename,
        )
        mode = 'r:*'
    tar = tarfile.open(filename, mode)
    try:
        leading = has_leading_dir([
            member.name for member in tar.getmembers()
        ])
        for member in tar.getmembers():
            fn = member.name
            if leading:
                # https://github.com/python/mypy/issues/1174
                fn = split_leading_dir(fn)[1]  # type: ignore
            path = os.path.join(location, fn)
            if not is_within_directory(location, path):
                message = (
                    'The tar file ({}) has a file ({}) trying to install '
                    'outside target directory ({})'
                )
                raise InstallationError(
                    message.format(filename, path, location)
                )
            if member.isdir():
                ensure_dir(path)
            elif member.issym():
                try:
                    # https://github.com/python/typeshed/issues/2673
                    tar._extract_member(member, path)  # type: ignore
                except Exception as exc:
                    # Some corrupt tar files seem to produce this
                    # (specifically bad symlinks)
                    logger.warning(
                        'In the tar file %s the member %s is invalid: %s',
                        filename, member.name, exc,
                    )
                    continue
            else:
                try:
                    fp = tar.extractfile(member)
                except (KeyError, AttributeError) as exc:
                    # Some corrupt tar files seem to produce this
                    # (specifically bad symlinks)
                    logger.warning(
                        'In the tar file %s the member %s is invalid: %s',
                        filename, member.name, exc,
                    )
                    continue
                ensure_dir(os.path.dirname(path))
                with open(path, 'wb') as destfp:
                    shutil.copyfileobj(fp, destfp)
                fp.close()
                # Update the timestamp (useful for cython compiled files)
                # https://github.com/python/typeshed/issues/2673
                tar.utime(member, path)  # type: ignore
                # member have any execute permissions for user/group/world?
                if member.mode & 0o111:
                    # make dest file have execute for user/group/world
                    # no-op on windows per python docs
                    os.chmod(path, (0o777 - current_umask() | 0o111))
    finally:
        tar.close()


def unpack_file(
        filename,  # type: str
        location,  # type: str
        content_type=None,  # type: Optional[str]
):
    # type: (...) -> None
    filename = os.path.realpath(filename)
    if (
        content_type == 'application/zip' or
        filename.lower().endswith(ZIP_EXTENSIONS) or
        zipfile.is_zipfile(filename)
    ):
        unzip_file(
            filename,
            location,
            flatten=not filename.endswith('.whl')
        )
    elif (
        content_type == 'application/x-gzip' or
        tarfile.is_tarfile(filename) or
        filename.lower().endswith(
            TAR_EXTENSIONS + BZ2_EXTENSIONS + XZ_EXTENSIONS
        )
    ):
        untar_file(filename, location)
    else:
        # FIXME: handle?
        # FIXME: magic signatures?
        logger.critical(
            'Cannot unpack file %s (downloaded from %s, content-type: %s); '
            'cannot detect archive format',
            filename, location, content_type,
        )
        raise InstallationError(
            'Cannot determine archive format of {}'.format(location)
        )
