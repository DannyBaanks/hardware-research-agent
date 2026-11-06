# Hardware Portability Matrix: Cross-Platform Compatibility

> **What this document is (checked 2026-11-06).** A design matrix: for each hardware family and each
> privilege environment it states which interfaces the protocol *would* use and which stay out of
> bounds. It is not validation evidence. No run is recorded here, and the only machine IHRA has
> actually executed on is this AMD Zen 4 mobile host with an NVIDIA Ada GPU running Linux — see
> Phase 7 of [IMPLEMENTATION_PLAN §3](IMPLEMENTATION_PLAN.md), `NOT_DEMONSTRATED` for every other row.
> A "Supported Capabilities" cell therefore means *the discovery interface exists in the code*, not
> *it was proven on that silicon*.
>
> The privilege table is also target design in its second row. Today `core/` and `adapters/` contain
> no path that loads a module option, writes under `/etc`, installs a udev rule or restarts a
> service: `core/cpu_power.py` only globs `/etc/udev/rules.d`, `/etc/tmpfiles.d` and systemd unit
> names to detect a third-party mechanism already configured, and its single `systemctl` call is
> `is-active`, a status read. The rootless row's DBus profile switch is the one permitted state
> change, and `powerprofilesctl set` runs only through `core/tier1.py` with a person confirming the
> exact plan at this TTY (`DENIED` unattended). Elevated research is an operator action, not an IHRA
> capability, and `experiments/cpu-boost/run_boost_matrix.py` under `sudo` is explicitly outside this
> contract ([SAFETY_CONTRACTS §6.5](SAFETY_CONTRACTS.md)).

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
