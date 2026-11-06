# Case Study B: REA-CPU-01 / AMD Hawk Point Forensics

## 1. Context and Hardware Baseline
* **Platform:** HP Victus Laptop 15-fb3xxx
* **Processor:** AMD Ryzen 5 8645HS with Radeon 760M Graphics
* **Microarchitecture:** Zen 4 (Hawk Point / Phoenix refresh, TSMC 4nm)
* **CPUID:** Family 25 (0x19), Model 117 (0x75), Stepping 2
* **Topology:** 6 Cores, 12 Threads (1 CCX, SMT enabled)
* **Vector Units:** Native AVX-512 (F, DQ, BW, VL, CD, VNNI, BF16)
* **Operating System:** Ubuntu 24.04 LTS, Linux kernel `7.0.0-42-generic`
* **Evidence Directory:** `evidence/REA-CPU-01/` in the ISyCo research monorepo, which is not published as part of IHRA — the pack does not ship in this repository, so a reader cannot re-verify it from a clone; the line below records what was verified where it lives
* **Cryptographic Verification:** 20/20 artifacts verified with SHA-256 (`SHA256SUMS.txt`)

---

## 2. Chronological Investigation Trace

### Step 1: Baseline Gate 0 Audit
Prior to any benchmark execution, the agent captured:
* Active processes: Detected background jobs (`agy` at 308% CPU, `ISyCoLang` pydecimal benchmark at 100% CPU).
* Initial power profile: Captured via `powerprofilesctl get` as **`power-saver`** (mapping to ACPI `platform_profile = low-power` and `amd_pstate` EPP `power`).
* Thermal baseline: Core temperature ~71.6 °C (`k10temp`), dual fans at 3185 / 2725 RPM.
* Memory baseline: 14 GiB RAM physical + 18.8 GiB total Swap (including 14.8 GiB ZRAM).

### Step 2: Front A — Power Profile Forensics Matrix
* **Hypothesis A1:** The default `power-saver` (`low-power`) profile constrains sustained all-core multi-thread clocks and throughput on the Ryzen 5 8645HS compared to `balanced` and `performance`.
* **Experimental Design:** Counterbalanced 9-run sequence (3 repetitions per profile) across `power-saver`, `balanced`, and `performance`.
* **Execution Sequence:**
  * Round 1: `power-saver` → `balanced` → `performance`
  * Round 2: `performance` → `balanced` → `power-saver`
  * Round 3: `balanced` → `power-saver` → `performance`
* **Measured Workloads:**
  1. ISyInfer AVX-512 Native: `bench_wasm_m0_native` (6 threads, Znver4, vector quantization and dot-products).
  2. Independent Microbenchmark: `cpu_microbench.py` (12 threads multi-core float/polynomial computation with SHA-256 result hash).
* **Observed Metrics:**
  * In `power-saver`, clocks throttled down to **3554–3893 MHz** (mean: 3758.5 MHz) as STAPM power budget exhausted; throughput averaged **4.70 MOPS**.
  * In `performance`, clocks remained pinned at **4232–4260 MHz** (mean: 4232.6 MHz, a **+12.6% frequency uplift**); throughput reached **4.95 MOPS (+5.3% speedup)**.
  * ISyInfer `Q8_0` quantization latency improved from **1733.0 ns to 1646.3 ns (5.0% faster)**.
  * **Anti-Fake Parity:** All 9 runs yielded the exact same result hash `862cb508588878ed...`.
  * **State Restoration:** Upon completion of run 9, the harness cleanly restored `powerprofilesctl set power-saver`.
* **Verdict:** **`DEMONSTRATED`** (PASS A).

### Step 3: Front B — AMD XDNA NPU Discovery
* **Hypothesis B1:** The integrated AMD XDNA NPU can execute compatible local AI workloads without consuming CUDA or primary CPU compute cores.
* **Findings:**
  * Hardware presence: Device detected on PCI bus `0000:07:00.1` (`1022:1502`), 16 TOPS Int8 AIE2 array.
  * Kernel driver: `amdxdna` loaded firmware `amdnpu/1502_00/npu.sbin` (fw `1.5.2.380`) and exposed character device `/dev/accel/accel0`.
  * User-space status: AMD XRT and Vitis-AI Execution Provider were **absent**.
* **Anti-Fake Rigor:**
  $$\text{NPU Detected} \neq \text{NPU Ready for Inference} \neq \text{Model Executing}$$
* **Verdict:** Hardware and kernel enablement are **`DEMONSTRATED`**; current user-space inference is **`REFUTED`** (pending runtime installation).

### Step 4: Front C — ACPI & HP Platform Forensics (Read-Only)
* **Scope:** 100% Read-Only audit. Zero writes to ACPI or fan registers.
* **Findings:**
  * 15 WMI GUID endpoints cataloged under `/sys/bus/wmi/devices/` including `hp-bioscfg` (HP Sure Start) and `wmi-bmof` (Binary MOF metadata).
  * Dual fan tachometers are exposed via `hp-wmi` (`hwmon7/fan1_input` and `fan2_input`).
  * Fan control mode (`pwm1_enable`) is locked in mode **`2` (Automatic EC closed-loop)**. Software manual override requires untested EC register manipulation and was classified as `REQUIRES_SEPARATE_AUTHORIZATION`.
  * Platform power profile switching is mediated by `amd-pmf` bound to ACPI node `AMDI0102:00`.
* **Verdict:** **`DEMONSTRATED`** (PASS C).

### Step 5: Front D — SMU Hypothesis Ledger
* **Objective:** Evaluate if the SMU intervenes in observed performance limits without modifying registers.
* **Findings:**
  * Evaluated AMD nominal specifications (35–54 W TDP) against HP OEM implementation constraints (STAPM skin-temperature throttling).
  * Investigated Curve Optimizer undervolting via SMU mailbox registers (PCI `00:00.0` offsets `0x60/0x64`).
  * Confirmed that direct user-space port writes bypass kernel synchronization and carry severe risk of PCIe freeze and CPU lockup.
  * No safe, documented user-space sysfs write interface exists on Linux.
* **Verdict:** **`NO_SAFE_WRITE_PATH_IDENTIFIED`** (Completed with safety preservation).

---

## 3. Summary of REA-CPU-01 Claims & Verdicts

| Claim ID | Focus Area | Claim Statement | Status | Ground Truth Evidence |
| :---: | :---: | :--- | :---: | :--- |
| **A.1** | Power Profiles | `power-saver` constrains multi-core frequency. | `DEMONSTRATED` | Clocks throttle to 3554–3893 MHz vs 4232–4260 MHz (+12.6% in `performance`). |
| **A.2** | Power Profiles | `performance` provides measurable compute speedup with mathematical parity. | `DEMONSTRATED` | Throughput +5.3% (4.95 vs 4.70 MOPS), Q8_0 -5.0% latency, SHA-256 invariant. |
| **B.1** | NPU (XDNA) | Physical XDNA NPU exists on PCI bus. | `DEMONSTRATED` | `0000:07:00.1` (`1022:1502`), 16 TOPS Int8 array. |
| **B.2** | NPU (XDNA) | Kernel driver initializes device node. | `DEMONSTRATED` | `amdxdna` loaded fw `1.5.2.380` on `/dev/accel/accel0`. |
| **B.3** | NPU (XDNA) | Models can run locally in current state. | `REFUTED` | User-space XRT / Vitis-AI stack is not installed. |
| **C.1** | ACPI / HP | HP WMI and thermal endpoints mapped. | `DEMONSTRATED` | 15 WMI GUIDs, dual fans on `hwmon7`, automatic EC control. |
| **C.2** | ACPI / HP | Fan speeds can be manually overridden safely. | `NOT_DEMONSTRATED` | `REQUIRES_SEPARATE_AUTHORIZATION` (EC registers locked). |
| **D.1** | SMU | STAPM power budget mediates throttling. | `DEMONSTRATED` | Controlled via `amd-pmf` SMU calls on profile switch. |
| **D.2** | SMU | Silicon physically supports Curve Optimizer. | `INFERRED` | Parity with Zen 4 architecture; locked by HP BIOS. |
| **D.3** | SMU | Safe user-space write path exists on Linux. | `REFUTED` | `NO_SAFE_WRITE_PATH_IDENTIFIED` (port writes unsafe). |

---

> **Correction (2026-11-04): the ~5 % was not the ceiling.** A re-examination (REA-CPU-02) found three
> problems with the campaign above, none visible in its own verdicts:
> 1. **CPU boost was disabled** by a local rule (`/etc/tmpfiles.d/amd-boost.conf` writing
>    `cpufreq/boost=0` at boot). The `boost: 0` line in the inventory was a switch turned off, not a
>    firmware cap; clocks sat at ~4.27 GHz while the hardware maximum is ~5.0 GHz.
> 2. **The benchmark was GIL-bound.** `cpu_microbench.py` runs Python threads, which the GIL serialises, so
>    "12 threads" is roughly one core.
> 3. **Background load.** Re-run on an idle machine, the same benchmark (same hash) gives 6.66 vs 4.95 MOPS.
>
> The power-profile result (A.1/A.2) stands as measured under those conditions.

### Follow-up: REA-CPU-02 boost on/off matrix (AC, 36 jobs, hashes invariant)

| Workload | Boost off | Boost on | Gain | Package W | Energy per op |
| :--- | ---: | ---: | ---: | ---: | ---: |
| Integer, 1 thread | 0.530 Gops/s | 0.613 | **+15.7 %** | 8.4 → 14.2 | +46 % |
| AVX-512 FMA, 1 thread | 67.8 GFLOP/s | 78.7 | **+16.1 %** | 8.9 → 15.8 | +52 % |
| Integer, 12 threads | 5.08 Gops/s | 5.49 | **+8.1 %** | 21.5 → 32.3 | +39 % |
| AVX-512 FMA, 12 threads | 408 GFLOP/s | 444 | **+8.8 %** | 27.7 → 41.4 | +38 % |
| Memory triad | 31.2 GB/s | 31.4 | +0.4 % | 22.3 → 36.5 | +63 % |

On battery the single-thread gain is the same, but the all-core gain vanishes (package capped near 22 W).
RAM is 2×8 GB DDR5-5600 in dual channel, so the suspected single-channel bottleneck was **refuted**.
The owner chose a "smart boost" (on with charger, off on battery): [`recipes/smart-boost`](../recipes/smart-boost/README.md).
Reproduce on any Linux machine with [`experiments/cpu-boost`](../experiments/cpu-boost/README.md).

**Lesson:** the same as the GPU case. A setting that *reports* a limit ("max 4.3 GHz") is not
the hardware limit. Find out who set it before calling it a ceiling.

---

## 4. Methodological Lessons for Hardware Agents

1. **Negative Results Are Valid Deliverables:**
   Arriving at `NO_SAFE_WRITE_PATH_IDENTIFIED` for the SMU was an explicit success: it protected hardware from bricking while cataloging the exact boundary between kernel-supported APIs and unsafe register poking.
2. **Always Measure A/B in Alternating Order:**
   Running tests in fixed order (e.g. A then B then C) produces thermal creep where later runs run hotter and throttle sooner. Alternating sequences (Round 1: A-B-C, Round 2: C-B-A, Round 3: B-A-C) cancels thermal drift.
3. **Always Check Mathematical Determinism:**
   A compute run that executes faster by computing corrupt or garbage results is a failure. Always hash output buffers with SHA-256.
4. **Always Restore Initial State:**
   An agent must never leave the user's host in an altered energy profile or state without explicit user instruction.
