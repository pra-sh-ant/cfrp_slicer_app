"""Background slicer worker (QThread) — keeps the UI responsive while
slicing large parts.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.machine import MachineProfile


class SlicerWorker(QThread):
    finished = Signal(object)   # SliceResult on success
    failed = Signal(str)        # error message on failure

    def __init__(self, params: dict, layup: list[float], machine: MachineProfile):
        super().__init__()
        self._params = params
        self._layup = layup
        self._machine = machine

    def run(self):
        try:
            config = from_legacy_dict(self._params, layup=self._layup)
        except ConfigError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(f"Config error: {exc}")
            return
        try:
            result = slice_part(config, self._machine)
        except ValueError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Internal slicer error: {exc}")
        else:
            self.finished.emit(result)
