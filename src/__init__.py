"""Email RAG — Search your Outlook emails locally."""

from __future__ import annotations

import sys

_MIN_PYTHON = (3, 11)


def _require_supported_python() -> None:
    """Fail fast with a clear message on unsupported interpreters.

    The project metadata declares ``requires-python >=3.11``.  In unsupported
    runtimes, downstream imports can fail later with obscure errors (for example
    ``datetime.UTC`` on Python 3.10). Raise a direct and actionable error early.
    """

    if sys.version_info >= _MIN_PYTHON:
        return
    required = f"{_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}"
    current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    raise RuntimeError(
        "outlook-email-rag requires Python "
        f"{required}+ (current interpreter: {current}). "
        "Please use a Python 3.11+ virtual environment."
    )


_require_supported_python()
