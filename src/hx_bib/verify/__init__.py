"""Citation verification subpackage."""

from .checker import (  # noqa: F401
    FAIL,
    INFO,
    MINOR,
    OK,
    WARN,
    CheckResult,
    SEVERITY_LABEL,
    SEVERITY_ORDER,
    prefetch_arxiv,
    retry_failed,
    verify_entries,
    verify_entry,
)
from .net import Net  # noqa: F401
