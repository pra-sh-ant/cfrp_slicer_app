"""cfrp_core: UI-free continuous-fibre slicing library.

Typical use:
    from cfrp_core import ProcessConfig, slice_part
    result = slice_part(ProcessConfig())
"""
from .config import ConfigError, ProcessConfig, build_config
from .constraints import ResolvedConfig, resolve_process_constraints
from .legacy import from_legacy_dict
from .machine import DEFAULT_MACHINE, MachineProfile, load_machine_profile
from .pipeline import SliceResult, slice_part

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_MACHINE", "ConfigError", "MachineProfile", "ProcessConfig", "ResolvedConfig",
    "SliceResult", "build_config", "from_legacy_dict", "load_machine_profile",
    "resolve_process_constraints", "slice_part",
]
