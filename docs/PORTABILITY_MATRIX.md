# Hardware Portability Matrix: Cross-Platform Compatibility

## 1. Design Philosophy: Graceful Degradation
A portable hardware research agent must function seamlessly across diverse hardware architectures and privilege environments without crashing when a specific subsystem is missing.

**Rule of Portability:** Every adapter must inspect the physical environment upon initialization. If target hardware, drivers, or capabilities are absent, the adapter emits `CapabilityStatus.NOT_APPLICABLE` and cleanly disables associated tests without throwing exceptions or blocking other subsystems.

---

## 2. Architecture & Silicon Target Matrix

| Hardware Family | Architecture / Platform | Discovery Interface | Supported Capabilities | Unsupported / Out-of-Bounds |
| :--- | :--- | :--- | :--- | :--- |
| **AMD Zen 4 / Zen 5 Mobile** | Hawk Point / Phoenix / Strix Point APU | `sysfs`, `amd_pstate`, `amd-pmf`, `k10temp` | Core inventory, AVX-512 VNNI/BF16 detection, ACPI platform profile switching (`powerprofilesctl`). | Direct SMU/SMN register poking, custom STAPM hacking. |
| **AMD Zen 3 / Zen 4 Desktop** | Vermeer / Raphael (AM4 / AM5) | `sysfs`, `cpufreq`, `edac`, `k10temp` | Multi-CCX topology, PBO curve telemetry (read-only), PPT limit telemetry. | Manual voltage FID/VID overrides from userspace. |
| **Intel Core 12th–14th Gen** | Alder Lake / Raptor Lake (Hybrid P+E) | `sysfs`, `intel_pstate`, `coretemp`, `intel-rapl` | P-core / E-core asymmetric topology, AVX2, thermal throttle flags, RAPL powercap reading. | AVX-512 on fused client chips, BCLK overclocking. |
| **ARM64 (Apple Silicon)** | M1 / M2 / M3 (Asahi Linux) | `sysfs`, `/proc/cpuinfo`, `apple-cpufreq` | Performance / Efficiency cluster mapping, NEON, AMX matrix unit presence. | Apple proprietary SMC overrides, custom voltage states. |
| **ARM64 (SBC / Server)** | Rockchip RK3588 / Ampere Altra | `sysfs`, `opp` (Operating Performance Points) | Big.LITTLE topology, RK NPU detection (`rknpu`), DVFS governor inspection. | Overvoltage beyond device-tree specifications. |
| **NVIDIA dGPU** | Turing, Ampere, Ada Lovelace | NVML, `nvidia-smi`, sysfs DRM, PCIe BAR | VRAM topology, SM count, power state (P0–P8), temperature, tensor issue rate benchmarking. | Desktop VBIOS flashing, cross-device payload injection. |
| **AMD Radeon iGPU / dGPU** | RDNA2 / RDNA3 (amdgpu) | `sysfs`, `drm/cardX`, ROCm SMI | Compute unit count, shader engine topology, memory clock, power profile modes. | VBIOS power play table injection without kernel signatures. |
| **Neural Processing Units** | AMD XDNA 1 / 2, Intel NPU | `/dev/accel/*`, `amdxdna`, `intel_vpu` | Device detection, firmware version check, DRM accel character node verification. | Direct unverified ioctl fuzzing without user-space runtime. |

---

## 3. Privilege & Environment Matrix

| Execution Environment | User Permissions | Permitted Actions | Denied Actions (Graceful Skip) |
| :--- | :--- | :--- | :--- |
| **Rootless Bare-Metal (Standard Desktop)** | Unprivileged User (`$UID \ge 1000`) | Full hardware discovery, sensor reads, user-space benchmarks, DBus `powerprofilesctl` switches. | Modifying `/etc/modprobe.d/`, loading kernel modules, writing systemd units. |
| **Elevated Bare-Metal (With Sudo Grant)** | Sudoer (Explicit human authorization per step) | Reversible module options, persistent tempfiles configurations, service restarts. | Flashing firmware ROMs, direct MSR writes, bypassing ACPI thermal limits. |
| **Container / Rootless Docker** | Isolated Container namespace | Local compute benchmarking, virtualized CPU topology. | All PCI/ACPI access skipped (`NOT_APPLICABLE`); physical sensors masked. |
| **Virtual Machine (KVM / QEMU)** | Virtualized Guest OS | CPU feature passthrough verification, virtual memory stress testing. | Physical motherboard WMI / EC skipped (`NOT_APPLICABLE`). |
