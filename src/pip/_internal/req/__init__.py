# The following comment should be removed at some point in the future.
# mypy: strict-optional=False

from __future__ import absolute_import

import logging
from multiprocessing.pool import Pool

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
    parallel=False,  # type: bool
    *args,  # type: Any
    **kwargs  # type: Any
):
    # type: (...) -> List[InstallationResult]
    """
    Install everything in the given list.

    (to be called after having downloaded and unpacked the packages)
    """

    if to_install:
        logger.info(
            'Installing collected packages: %s',
            ', '.join([req.name for req in to_install]),
        )

    # pre allocate installed package names
    installed = [None] * len(to_install)
    install_args = [install_options, global_options, args, kwargs]

    if parallel:
        # first let's try to install in parallel, if we fail we do it by order.
        pool = Pool()
        try:
            installed = pool.starmap(__single_install, [(install_args, r) for r in to_install])
        except:
            # we will reinstall sequentially
            pass
        pool.close()
        pool.join()

    with indent_log():
        for i, requirement in enumerate(to_install):
            if installed[i] is None:
                installed[i] = __single_install(install_args, requirement, allow_raise=True)

    return [i for i in installed if i is not None]


def __single_install(args, a_requirement, allow_raise=False):
    if a_requirement.should_reinstall:
        logger.info('Attempting uninstall: %s', a_requirement.name)
        with indent_log():
            uninstalled_pathset = a_requirement.uninstall(
                auto_confirm=True
            )
    try:
        a_requirement.install(
            args[0],   # install_options,
            args[1],   # global_options,
            *args[2],  # *args,
            **args[3]  # **kwargs
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
        return InstallationResult(a_requirement.name)

    return None
