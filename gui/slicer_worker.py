"""Background slicer worker (QThread) — keeps the UI responsive while
slicing large parts. With `board` = (BoardDesign, DiwConfig) it slices the
structural PCB (fibre laminate + DIW electronics) instead.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.machine import MachineProfile


class SlicerWorker(QThread):
    finished = Signal(object)   # SliceResult (or BoardResult) on success
    failed = Signal(str)        # error message on failure

    def __init__(self, params: dict, layup: list[float], machine: MachineProfile, board=None):
        super().__init__()
        self._params = params
        self._layup = layup
        self._machine = machine
        self._board = board
        self._cancelled = False

    def cancel(self):
        """Request cancellation. The thread will finish its current Python call
        before checking, so it may emit finished/failed after this returns."""
        self._cancelled = True

    def run(self):
        try:
            config = from_legacy_dict(self._params, layup=self._layup)
        except ConfigError as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
            return
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(f"Config error: {exc}")
            return

        if self._cancelled:
            return

        try:
            if self._board is not None:
                from cfrp_core.board import slice_structural_board
                result = slice_structural_board(self._board[0], config, self._board[1], self._machine)
            else:
                result = slice_part(config, self._machine)
        except ValueError as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(f"Internal slicer error: {exc}")
        else:
            if not self._cancelled:
                self.finished.emit(result)
