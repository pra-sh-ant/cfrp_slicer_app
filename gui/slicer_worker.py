"""Background slicer worker (QThread) — keeps the UI responsive while
slicing large parts. With `board` = (BoardDesign, DiwConfig) it slices the
structural PCB (fibre laminate + DIW electronics) instead.

With `view` = (max_points, core_mode) it also prepares what the 3D view
draws (thinned data and tube meshes, toolpath_mesh.prepare) and attaches it
to the result as `prepared_view`, so a large part does not freeze the
window while its mesh is built.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.machine import MachineProfile


class SlicerWorker(QThread):
    finished = Signal(object)   # SliceResult (or BoardResult) on success
    failed = Signal(str)        # error message on failure

    def __init__(self, params: dict, layup: list[float], machine: MachineProfile, board=None, view=None):
        super().__init__()
        self._params = params
        self._layup = layup
        self._machine = machine
        self._board = board
        self._view = view
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
                mode = self._board[2] if len(self._board) > 2 else "structural"
                result = slice_structural_board(self._board[0], config, self._board[1], self._machine, mode=mode)
            else:
                result = slice_part(config, self._machine)
        except ValueError as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(f"Internal slicer error: {exc}")
        else:
            if self._view is not None and self._board is None and not self._cancelled:
                result.prepared_view = _prepare(result, *self._view)
            if not self._cancelled:
                self.finished.emit(result)


def _prepare(result, max_points, core_mode):
    """The view's data for a fibre result (None if it cannot be built:
    the viewer then builds it itself)."""
    try:
        from .toolpath_mesh import prepare
        c = result.config
        bead = min(float(c.material.needle_dia_mm), float(c.track_gap_mm))
        return prepare(result.trajectory, bead, float(c.layer_height_mm), max_points, core_mode)
    except Exception:
        return None
