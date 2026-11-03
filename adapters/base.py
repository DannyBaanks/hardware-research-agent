from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass

class CapabilityStatus(Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"

@dataclass
class AdapterProbeResult:
    status: CapabilityStatus
    device_id: Optional[str] = None
    telemetry: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None

class HardwareAdapter:
    """Base class for all hardware interaction adapters."""
    name: str = "base_adapter"
    is_read_only: bool = True

    def probe(self) -> AdapterProbeResult:
        """Inspects environment and determines whether this adapter is applicable."""
        raise NotImplementedError

    def read_telemetry(self) -> Dict[str, Any]:
        """Reads non-invasive sensor or configuration state."""
        return {}
