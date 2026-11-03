# SPDX-License-Identifier: MIT
"""
GPU Unlock Ada Adapter for Hardware Research Agent.
Provides hardware capability discovery and canary verification for NVIDIA Ada architectures
(specifically AD107M / RTX 4050 Laptop and related GPUs, including D3cold power management).
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from .base import HardwareAdapter, CapabilityStatus, AdapterProbeResult


class AdaUnlockAdapter(HardwareAdapter):
    name = "ada_gpu_unlock"
    is_read_only = True

    def __init__(self, ada_path: Optional[str] = None):
        self.ada_path = ada_path or os.environ.get(
            "ADA_UNLOCK_HOME",
            str(Path.home() / "Development" / "gpu-unlock-hp" / "gpu-unlock-ada")
        )

    def probe(self) -> AdapterProbeResult:
        if shutil.which("nvidia-smi") is None and not os.path.exists("/dev/nvidia0"):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="NVIDIA hardware not present or driver uninitialized"
            )

        details = {}
        # Check PCI device IDs for Ada family (AD107 = 28a1, 28e0, etc., AD106 = 2820, AD104 = 2782...)
        pci_dev_path = "/sys/bus/pci/devices"
        ada_pci_found = False
        pci_id = "unknown"

        if os.path.exists(pci_dev_path):
            try:
                for dev in os.listdir(pci_dev_path):
                    dev_vendor = os.path.join(pci_dev_path, dev, "vendor")
                    dev_device = os.path.join(pci_dev_path, dev, "device")
                    if os.path.exists(dev_vendor) and os.path.exists(dev_device):
                        with open(dev_vendor) as f:
                            vendor = f.read().strip()
                        with open(dev_device) as f:
                            device = f.read().strip()
                        if vendor == "0x10de":
                            pci_id = f"{vendor}:{device}"
                            # 0x28a1 is AD107M (RTX 4050 Mobile), 0x28e0 is RTX 4060 Mobile, etc.
                            if device.lower() in ("0x28a1", "0x28e0", "0x2820", "0x2782", "0x2684"):
                                ada_pci_found = True
                                details["pci_slot"] = dev
                                details["device_id"] = pci_id
                                break
            except Exception as e:
                details["probe_pci_error"] = str(e)

        details["ada_pci_match"] = ada_pci_found
        details["has_unlock_repo"] = os.path.exists(self.ada_path)

        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED if ada_pci_found else CapabilityStatus.UNSUPPORTED,
            device_id=pci_id,
            telemetry=details
        )

    def read_d3cold_status(self) -> Dict[str, Any]:
        """
        Inspects whether NVIDIA kernel module parameter DynamicPowerManagement is configured
        to prevent D3cold power rail collapse (the key discovery in PR #134).
        """
        dpm_path = "/sys/module/nvidia/parameters/DynamicPowerManagement"
        if os.path.exists(dpm_path):
            try:
                with open(dpm_path) as f:
                    val = f.read().strip()
                # 0 = DynamicPowerManagement=0x00 (disabled, prevents D3cold rail reset)
                # 1 = coarse-grained D3hot
                # 2 = fine-grained D3cold
                return {
                    "dynamic_power_management_raw": val,
                    "d3cold_prevented": val in ("0", "0x00"),
                    "status": "SECURE_PERSISTENT" if val in ("0", "0x00") else "VULNERABLE_TO_D3COLD_COLLAPSE"
                }
            except Exception as e:
                return {"error": str(e)}
        return {"status": "NOT_EXPOSED", "reason": "nvidia module parameter file not accessible"}

    def read_telemetry(self) -> Dict[str, Any]:
        probe = self.probe()
        d3cold = self.read_d3cold_status()

        telemetry = {
            "probe_status": probe.status.value,
            "device_id": probe.device_id,
            "d3cold_status": d3cold,
            "has_unlock_toolkit": os.path.exists(self.ada_path)
        }

        # Query nvidia-smi if available
        if shutil.which("nvidia-smi"):
            try:
                res = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,driver_version,pstate,temperature.gpu,power.draw",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3
                )
                if res.returncode == 0:
                    parts = [p.strip() for p in res.stdout.strip().split(",")]
                    telemetry["gpu_name"] = parts[0] if len(parts) > 0 else "unknown"
                    telemetry["driver_version"] = parts[1] if len(parts) > 1 else "unknown"
                    telemetry["pstate"] = parts[2] if len(parts) > 2 else "unknown"
                    telemetry["temp_c"] = float(parts[3]) if len(parts) > 3 else 0.0
                    telemetry["power_w"] = float(parts[4]) if len(parts) > 4 else 0.0
            except Exception as e:
                telemetry["smi_error"] = str(e)

        return telemetry
