import os
import shutil
import subprocess
from typing import Dict, Any
from .base import HardwareAdapter, CapabilityStatus, AdapterProbeResult

class LinuxInventoryAdapter(HardwareAdapter):
    name = "linux_inventory"
    is_read_only = True

    def probe(self) -> AdapterProbeResult:
        if not os.path.exists("/proc/cpuinfo"):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="/proc/cpuinfo not available (non-Linux environment)"
            )
        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED,
            device_id="linux_host_root"
        )

    def read_telemetry(self) -> Dict[str, Any]:
        info = {"cpu_model": "unknown", "cores": 0}
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name") and info["cpu_model"] == "unknown":
                        info["cpu_model"] = line.split(":", 1)[1].strip()
                    if line.startswith("processor"):
                        info["cores"] += 1
        except Exception as e:
            info["error"] = str(e)
        return info


class LinuxCpuPerfAdapter(HardwareAdapter):
    name = "linux_cpu_perf"
    is_read_only = False

    def probe(self) -> AdapterProbeResult:
        has_ctl = shutil.which("powerprofilesctl") is not None
        has_sysfs = os.path.exists("/sys/firmware/acpi/platform_profile")
        if not (has_ctl or has_sysfs):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="Neither powerprofilesctl nor platform_profile sysfs interface found"
            )
        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED,
            device_id="acpi_platform_profile"
        )

    def get_current_profile(self) -> str:
        if shutil.which("powerprofilesctl"):
            try:
                res = subprocess.run(["powerprofilesctl", "get"], capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                pass
        pp_path = "/sys/firmware/acpi/platform_profile"
        if os.path.exists(pp_path):
            try:
                with open(pp_path) as f:
                    return f.read().strip()
            except Exception:
                pass
        return "unknown"


class NvidiaTelemetryAdapter(HardwareAdapter):
    name = "nvidia_telemetry"
    is_read_only = True

    def probe(self) -> AdapterProbeResult:
        if shutil.which("nvidia-smi") is None and not os.path.exists("/dev/nvidia0"):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="NVIDIA drivers or devices not detected on host"
            )
        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED,
            device_id="nvidia_gpu_adapter"
        )

    def read_telemetry(self) -> Dict[str, Any]:
        if shutil.which("nvidia-smi"):
            try:
                res = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,pstate,temperature.gpu,power.draw", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3
                )
                if res.returncode == 0:
                    parts = [p.strip() for p in res.stdout.strip().split(",")]
                    return {
                        "gpu_name": parts[0] if len(parts) > 0 else "unknown",
                        "pstate": parts[1] if len(parts) > 1 else "unknown",
                        "temp_c": float(parts[2]) if len(parts) > 2 else 0.0,
                        "power_w": float(parts[3]) if len(parts) > 3 else 0.0,
                    }
            except Exception as e:
                return {"error": str(e)}
        return {"status": "device_present_no_smi"}


class AmdXdnaDiscoveryAdapter(HardwareAdapter):
    name = "amd_xdna_discovery"
    is_read_only = True

    def probe(self) -> AdapterProbeResult:
        pci_node = "/sys/bus/pci/devices/0000:07:00.1"
        accel_node = "/dev/accel/accel0"
        if not (os.path.exists(pci_node) or os.path.exists(accel_node)):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="AMD XDNA IPU PCI device (07:00.1) or /dev/accel/accel0 not present"
            )
        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED,
            device_id="amd_xdna_ipu_1022:1502"
        )

    def read_telemetry(self) -> Dict[str, Any]:
        fw_path = "/sys/bus/pci/devices/0000:07:00.1/fw_version"
        fw = "unknown"
        if os.path.exists(fw_path):
            try:
                with open(fw_path) as f:
                    fw = f.read().strip()
            except Exception:
                pass
        return {
            "device_node": "/dev/accel/accel0",
            "driver": "amdxdna",
            "firmware_version": fw,
            "userspace_xrt_installed": shutil.which("xrt-smi") is not None
        }
