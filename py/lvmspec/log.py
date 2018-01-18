"""
lvmspec.log
============

This is a transitional dummy wrapper on ``lvmutil.log``.
"""
from __future__ import absolute_import
# from warnings import warn

from lvmutil.log import DEBUG, INFO, WARNING, ERROR, CRITICAL
from lvmutil.log import get_logger as _lvmutil_get_logger

def get_logger(*args, **kwargs):
    """Transitional dummy wrapper on ``lvmutil.log.get_logger()``.
    """
    # warn("lvmspec.log is deprecated, please use lvmutil.log.",
    #      DeprecationWarning)
    log = _lvmutil_get_logger(*args, **kwargs)
    log.warn("lvmspec.log is deprecated, please use lvmutil.log.")
    return log
