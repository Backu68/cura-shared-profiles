"""Uranium Job used for Eventide shared-library change detection.

Cura/UI objects never enter this module. A job only enumerates/stat's the selected
library path and returns an immutable observation to the extension.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from UM.Job import Job


@dataclass(frozen=True)
class LibraryScanResult:
    root: str
    signature: Optional[str]
    error: Optional[str] = None


class EventideLibraryScanJob(Job):
    """Run exactly one shared-library fingerprint in Uranium's JobQueue."""

    def __init__(self, scanner: Callable[[str], Optional[str]], root: str) -> None:
        super().__init__()
        self._scanner = scanner
        self._root = str(root)

    def run(self) -> None:
        try:
            self.setResult(LibraryScanResult(self._root, self._scanner(self._root)))
        except (OSError, ValueError, TypeError) as error:
            self.setResult(LibraryScanResult(self._root, None, repr(error)))
        except Exception as error:
            # This is the worker boundary. Preserve the unexpected exception on
            # the Job as well as in the result so Cura diagnostics can surface it.
            self.setError(error)
            self.setResult(LibraryScanResult(self._root, None, repr(error)))
