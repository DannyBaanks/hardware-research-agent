# SPDX-License-Identifier: MIT
"""
GPU Unlock Ada Adapter for Hardware Research Agent.
Provides read-only capability discovery for NVIDIA Ada GPUs covered by the
gpu-unlock-ada kernel module (including the AD107M RTX 4050 Laptop canary and
its runtime D3cold power-management trap).

Everything here is software-reported state (sysfs, procfs, nvidia-smi). None of
it is hardware readback or throughput proof; callers must keep the
NOT_DEMONSTRATED label until the triangulation in AGENTS.md is done.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from .base import HardwareAdapter, CapabilityStatus, AdapterProbeResult


# Exact PCI device IDs the gpu-unlock-ada module knows (kernel/gpu_unlock/
# prep_610.h + arch_610.h at fork commit 0da2bb6). Being listed here only means
# "the module has a profile for it"; most entries are experimental and gated.
ADA_UNLOCK_DEVICE_IDS = frozenset({
    "0x2684", "0x2685", "0x2689",                    # AD102
    "0x2702", "0x2704", "0x2705", "0x2709", "0x2757",  # AD103 / AD103M
    "0x2782", "0x2783", "0x2786",                    # AD104
    "0x2803", "0x2805",                              # AD106
    "0x2882", "0x28a0", "0x28a1", "0x28e0", "0x28e1",  # AD107 / AD107M
})

NVIDIA_PARAMS_PROC = "/proc/driver/nvidia/params"
NVIDIA_PARAMS_SYSFS = "/sys/module/nvidia/parameters"
GPU_UNLOCK_MODULE = "/sys/module/gpu_unlock"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _parse_smi_number(value: str) -> Optional[float]:
    """nvidia-smi prints '[N/A]' or 'N/A' for unsupported fields (e.g. laptop power limits)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AdaUnlockAdapter(HardwareAdapter):
    name = "ada_gpu_unlock"
    is_read_only = True

    def __init__(self, ada_path: Optional[str] = None, sysfs_root: str = ""):
        self.ada_path = ada_path or os.environ.get(
            "ADA_UNLOCK_HOME",
            str(Path.home() / "Development" / "gpu-unlock-hp" / "gpu-unlock-ada")
        )
        # Prefix for every sysfs/procfs path; tests point it at a fake tree.
        self.sysfs_root = sysfs_root
        self._slot: Optional[str] = None

    def _p(self, path: str) -> str:
        return self.sysfs_root + path

    def probe(self) -> AdapterProbeResult:
        if shutil.which("nvidia-smi") is None and not os.path.exists(self._p("/dev/nvidia0")):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="NVIDIA hardware not present or driver uninitialized"
            )

        details: Dict[str, Any] = {}
        pci_dev_path = self._p("/sys/bus/pci/devices")
        ada_pci_found = False
        pci_id = None
        try:
            entries = sorted(os.listdir(pci_dev_path))
        except OSError as e:
            entries = []
            details["probe_pci_error"] = str(e)

        for dev in entries:
            base = os.path.join(pci_dev_path, dev)
            vendor = _read_text(os.path.join(base, "vendor"))
            device = _read_text(os.path.join(base, "device"))
            pci_class = _read_text(os.path.join(base, "class")) or ""
            # Only display controllers (class 0x03xxxx); skip the GPU's HDMI audio function.
            if vendor != "0x10de" or not pci_class.startswith("0x03") or device is None:
                continue
            if pci_id is None:
                pci_id = f"{vendor}:{device}"
            if device.lower() in ADA_UNLOCK_DEVICE_IDS:
                ada_pci_found = True
                pci_id = f"{vendor}:{device}"
                self._slot = dev
                details["pci_slot"] = dev
                details["device_id"] = pci_id
                break

        details["ada_pci_match"] = ada_pci_found
        details["has_unlock_repo"] = os.path.exists(self.ada_path)

        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED if ada_pci_found else CapabilityStatus.UNSUPPORTED,
            device_id=pci_id or "unknown",
            telemetry=details
        )

    def _read_dpm_parameter(self) -> Optional[Dict[str, str]]:
        """NVIDIA exposes NVreg_* values in procfs, not in /sys/module/nvidia/parameters."""
        text = _read_text(self._p(NVIDIA_PARAMS_PROC))
        if text is not None:
            for line in text.splitlines():
                key, sep, value = line.partition(":")
                if sep and key.strip() == "DynamicPowerManagement":
                    return {"value": value.strip(), "source": NVIDIA_PARAMS_PROC}
        value = _read_text(self._p(NVIDIA_PARAMS_SYSFS + "/DynamicPowerManagement"))
        if value is not None:
            return {"value": value, "source": NVIDIA_PARAMS_SYSFS + "/DynamicPowerManagement"}
        return None

    def read_d3cold_status(self) -> Dict[str, Any]:
        """
        Reports whether the NVIDIA DynamicPowerManagement parameter is configured to
        keep the dGPU out of runtime D3cold (the trap documented in PR #134), plus the
        kernel-reported PCI power state of the matched GPU. Both are software views.
        """
        dpm = self._read_dpm_parameter()
        if self._slot is None:
            self.probe()
        pci_power_state = (
            _read_text(self._p(f"/sys/bus/pci/devices/{self._slot}/power_state"))
            if self._slot else None
        )
        if dpm is None:
            return {
                "status": "NOT_EXPOSED",
                "reason": "DynamicPowerManagement not found in /proc/driver/nvidia/params or sysfs",
                "pci_power_state_kernel_reported": pci_power_state,
                "hardware_state_verified": False,
            }
        val = dpm["value"]
        # 0 = disabled (no runtime D3cold), 1 = coarse-grained, 2 = fine-grained, 3 = driver default
        configured = val.lower() in ("0", "0x0", "0x00")
        return {
            "dynamic_power_management_raw": val,
            "source": dpm["source"],
            "driver_parameter_indicates_prevention": configured,
            "pci_power_state_kernel_reported": pci_power_state,
            "hardware_state_verified": False,
            "status": (
                "PARAMETER_CONFIGURED_NOT_HARDWARE_VERIFIED"
                if configured
                else "PARAMETER_DOES_NOT_INDICATE_PREVENTION"
            ),
        }

    def read_module_status(self) -> Dict[str, Any]:
        """Read-only view of the gpu_unlock kernel module; 'loaded' is not 'unlocked'."""
        base = self._p(GPU_UNLOCK_MODULE)
        if not os.path.isdir(base):
            return {"loaded": False}
        params = {}
        pdir = os.path.join(base, "parameters")
        try:
            for name in sorted(os.listdir(pdir)):
                value = _read_text(os.path.join(pdir, name))
                if value is not None:
                    params[name] = value
        except OSError:
            pass
        return {
            "loaded": True,
            "version": _read_text(os.path.join(base, "version")),
            "parameters": params,
            "unlock_state_verified": False,
            "note": "module presence and parameters are not proof of an active unlock; "
                    "measure throughput (diagnostics/bench) to demonstrate it",
        }

    def read_telemetry(self) -> Dict[str, Any]:
        probe = self.probe()
        d3cold = self.read_d3cold_status()

        telemetry: Dict[str, Any] = {
            "probe_status": probe.status.value,
            "device_id": probe.device_id,
            "d3cold_status": d3cold,
            "gpu_unlock_module": self.read_module_status(),
            "has_unlock_toolkit": os.path.exists(self.ada_path)
        }

        if shutil.which("nvidia-smi"):
            try:
                res = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,driver_version,pstate,temperature.gpu,power.draw",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3
                )
                if res.returncode == 0:
                    first = res.stdout.strip().splitlines()[0] if res.stdout.strip() else ""
                    parts = [p.strip() for p in first.split(",")]
                    telemetry["gpu_name"] = parts[0] if len(parts) > 0 and parts[0] else "unknown"
                    telemetry["driver_version"] = parts[1] if len(parts) > 1 else "unknown"
                    telemetry["pstate"] = parts[2] if len(parts) > 2 else "unknown"
                    telemetry["temp_c"] = _parse_smi_number(parts[3]) if len(parts) > 3 else None
                    telemetry["power_w"] = _parse_smi_number(parts[4]) if len(parts) > 4 else None
                else:
                    telemetry["smi_error"] = f"nvidia-smi exited {res.returncode}"
            except (OSError, subprocess.SubprocessError) as e:
                telemetry["smi_error"] = str(e)

        return telemetry
