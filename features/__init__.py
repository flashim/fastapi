"""Stable import surface for the FASTR-103 feature module.

The feature module lives in a file whose name is not a valid Python identifier,
so it is loaded from its path here and re-exported under a stable public name.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

_module_name = "fastr_103"
_spec = importlib.util.spec_from_file_location(
    _module_name, Path(__file__).parent / "fastr-103.py"
)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise ImportError("Could not load the FASTR-103 feature module.")
_module: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

DEFAULT_HEALTH_CHECK_METHODS = _module.DEFAULT_HEALTH_CHECK_METHODS
DEFAULT_HEALTH_CHECK_PATH = _module.DEFAULT_HEALTH_CHECK_PATH
DEFAULT_PROBE_TIMEOUT = _module.DEFAULT_PROBE_TIMEOUT
DegradedResponse = _module.DegradedResponse
HealthPayload = _module.HealthPayload
HealthResponse = _module.HealthResponse
Probe = _module.Probe
ProbeOutcome = _module.ProbeOutcome
ProbeResult = _module.ProbeResult
add_health_check = _module.add_health_check
build_payload = _module.build_payload
register_route = _module.register_route
remove_health_check = _module.remove_health_check
run_probes = _module.run_probes

__all__ = [
    "DEFAULT_HEALTH_CHECK_METHODS",
    "DEFAULT_HEALTH_CHECK_PATH",
    "DEFAULT_PROBE_TIMEOUT",
    "DegradedResponse",
    "HealthPayload",
    "HealthResponse",
    "Probe",
    "ProbeOutcome",
    "ProbeResult",
    "add_health_check",
    "build_payload",
    "register_route",
    "remove_health_check",
    "run_probes",
]