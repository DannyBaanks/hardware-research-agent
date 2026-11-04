# Case Study A: The NVIDIA Ada Lovelace D3cold Power-Rail Trap

## 1. Context and Hardware Baseline
* **Platform:** HP Victus Laptop 15-fb3xxx
* **Host Processor:** AMD Ryzen 5 8645HS with integrated Radeon 760M iGPU
* **Discrete GPU:** NVIDIA GeForce RTX 4050 Laptop GPU
* **Silicon:** AD107M rev a1, 20 Streaming Multiprocessors (SMs), 2560 CUDA cores, 6 GB GDDR6
* **PCI Identification:** `0000:01:00.0` (`10de:28a1`)
* **Software Environment:** Ubuntu 24.04 LTS, Linux kernel `7.0.0-42-generic`, NVIDIA Open Kernel Module `580.178.04`, CUDA 13.0
* **Upstream Milestone:** Pull Request [#134](https://github.com/JustAResearcher/gpu-unlock-ada/pull/134) on `JustAResearcher/gpu-unlock-ada`

---

## 2. Chronological Investigation Trace

### Step 1: Silicon Identification & Target Admission
The RTX 4050 Laptop GPU (`10de:28a1`) shares identical physical AD107M silicon with the desktop/OEM RTX 4050 mobile variant (`10de:28e1`). The project admitted `0x28a1` under `ad107_experimental` by extending:
* Gating and PCI table matching in `kernel/gpu_unlock/arch_610.h` and `unlock_610.c`
* Opt-in helpers in `tools/print_boot_option.py`
* Unit test harnesses in `tests/test_ad102_kernel.c` and `tests/test_main.c`

### Step 2: The Initial Hypothesis
Consumer Ada Lovelace GPUs contain physical tensor cores capable of dual issue rates (2.00×), but NVIDIA market-segments these cards by programming fused registers (`SS0`, `SS1`, `FEAT_OVR_PLM`, `FEAT_READOUT_1`) during boot to enforce half-rate throughput. Overriding these registers before GSP-RM firmware lock should unlock the full 2.00× tensor rate.

### Step 3: The Apparent Success (The First Illusion)
Upon boot with the unlock module:
* Kernel logs confirmed payload delivery and transactional acceptance.
* The vendor control query `read_sm_rates.py` (querying GSP-RM via client control `0x20801230`) returned:
  ```text
  0 0 0
  ```
  *(Indicating full-speed tensor rate on all three SM sub-pipelines).*

### Step 4: The Negative Control Failure
When physical microbenchmarks and workloads were executed against this "unlocked" card:
* `tensor_modes_driver_runner`: `f16_f32` produced **25.32 Top/s** (identical to stock 1.00× rate).
* `mma_bench 20000 1`: produced **25.5 TF/s** (identical to stock 1.00× rate).
* `gemm_bench`: 0% speedup.
* **The Anomaly:** Software reported *unlocked*, but silicon execution was *stock*. A lesser investigation would have either claimed a false victory based on software logs or dismissed the hardware as incapable.

### Step 5: Triangulation via Low-Level BAR0 Inspection
The team deployed `gpu_peek` to read the physical BAR0 hardware registers directly from PCIe configuration and MMIO space, bypassing the NVIDIA driver getter:
* **Expected Unlocked Registers:** `SS0=0x88888888`, `SS1=0x8`, `FEAT_OVR_PLM=0xffffffff`, `FEAT_READOUT_1=0x00000000`.
* **Actual Registers Read from Silicon:**
  ```text
  SS0 = 0x53123046
  SS1 = 0x3
  FEAT_READOUT_1 = 0x00410080
  ```
The hardware registers had reverted to their factory half-rate fuses!

### Step 6: Uncovering the Root Cause — The Optimus D3cold Power-Rail Trap
On hybrid/Optimus laptops:
1. The desktop environment (X11 / Wayland) runs on the integrated AMD Radeon 760M iGPU.
2. The default NVIDIA driver power management (`NVreg_DynamicPowerManagement=0x02`) places the idle discrete GPU into **runtime D3cold** (`ACPI LNXPOWER:00=0`, Video Memory: Off) within seconds after boot.
3. **The Hardware Reset:** Cutting power rails in D3cold causes volatile hardware registers (`SS0`, `SS1`) to lose power and reset back to hardwired fuse defaults upon wake.
4. **The GSP-RM Software Cache False Positive:** When `NVreg_PreserveVideoMemoryAllocations=1` is active, GSP-RM firmware restores its cached boot-time OBJGR state from system memory. Therefore, GSP-RM's internal data structures *believed* the card was unlocked, and `read_sm_rates.py` reported `0 0 0`, while physical execution was clamped at half rate!

### Step 7: The Controlled Resolution & Power Management Clarification
To prevent D3cold while maintaining hardware longevity:
1. Setting `options nvidia NVreg_DynamicPowerManagement=0x00` in `/etc/modprobe.d/nvidia-power.conf` inhibits runtime D3cold inside the NVIDIA driver.
2. **Why sysfs `power/control="on"` was rejected:** Forcing PCI sysfs `power/control="on"` at the udev level prevents the GPU from entering P8 idle, locking GSP-RM in P3 (1755 MHz / ~9.7 W) and generating `GPS ACPI DSM` / `NV_ERR_NO_MEMORY` warning spam whenever NVML opens `/dev/nvidia0`.
3. In contrast, `NVreg_DynamicPowerManagement=0x00` cleanly keeps the hardware registers intact while allowing GSP-RM to drop down to **P8 idle (210 MHz / ~1.6 W)** with silent cooling fans.

---

## 3. Verified Benchmark Results (PR #134)

| Metric / Kernel | Stock Rate | Unlocked Rate (No D3cold) | Speedup Factor | Numerical Parity |
| :--- | :--- | :--- | :---: | :---: |
| **`tensor_modes_driver_runner` f16_f32** | 25.320 Top/s | **50.682 Top/s** | **2.002×** | 0 / 270,336 mismatches |
| **`tensor_modes_driver_runner` tf32** | 12.660 Top/s | **25.281 Top/s** | **1.997×** | 0 / 270,336 mismatches |
| **`tensor_modes_driver_runner` u4_s32** (control)| 201.30 Top/s | 201.84 Top/s | 1.003× (invariant)| 0 / 270,336 mismatches |
| **`mma_bench` f16/f32 & bf16/f32** | 25.5 TF/s | **50.9 TF/s** | **1.996×** | Exact bitwise match |
| **`mma_bench` fp8/e4m3** | 50.9 TF/s | **101.4 TF/s** | **1.992×** | Exact bitwise match |
| **`mma_bench` dp4a / ffma** (controls) | 25.3 / 11.8 TF/s | 25.2 / 11.8 TF/s | 0.996× / 1.000× (invariant)| Exact bitwise match |
| **`gemm_bench` cuBLAS 4096³ FP16**, median (best) | 22.47 (22.55) TF/s | **30.54 (30.72) TF/s** | **+35.9%** (+36.2% best) | 6/6 exact |
| **`gemm_bench` cuBLAS 4096³ BF16**, median (best) | 21.48 (21.50) TF/s | **31.85 (34.75) TF/s** | **+48.3%** (+61.6% best) | 6/6 exact |

> **Correction (2026-11-03):** earlier versions of this table listed cuBLAS rates (29.4 → 40.0 and
> 23.7 → 38.3 TF/s) and dp4a/ffma controls (24.6 / 11.6) that appear in no evidence file. All values
> above are now taken from the raw `gemm_*.jsonl` and `mma_*.txt` files in
> [gpu-unlock-ada `docs/evidence/ada28a1/`](https://github.com/DannyBaanks/gpu-unlock-ada/tree/main/docs/evidence/ada28a1)
> (stock `run2` vs `run4_nod3cold`). Median is the honest headline; best-to-best is shown in parentheses.

---

## 4. Methodological Lessons for Hardware Agents

1. **Software Telemetry $\neq$ Physical Execution:**
   Never declare a performance improvement because a software register, configuration bit, or vendor tool says a feature is enabled. A software getter can report a cached state.
2. **The Invariance Requirement:**
   When testing an optimization for mode $X$, invariant controls (such as INT4 or scalar FFMA) must not change. If controls also speed up, the experiment has unmanaged confounders (e.g., clock frequency fluctuations, thermal bias).
3. **Power State Cycling is a Mandatory Test:**
   Any hardware modification that appears successful at boot must be tested across sleep, runtime power management (D3hot/D3cold), and idle transitions.
4. **Contextual Scope Limitation:**
   The resolution documented here is specific to the NVIDIA Ada AD107M silicon under Linux Optimus. It must never be applied blindly to desktop GPUs, other vendors, or other architectures.
