# Implementation Plan: From HRA-00 to Production Protocol

## 1. Phased Roadmap Overview

The transformation of ad-hoc hardware research into a standardized, portable agent protocol proceeds in 9 sequential phases. Advancement requires fulfilling explicit exit criteria.

---

## 2. Milestone Phasing

### Phase 0: Ground Truth & Evidence Ingestion (HRA-00 Baseline)
* **Goal:** Establish immutable references for historical case studies.
* **Tasks:**
  * Import verified evidence from `JustAResearcher/gpu-unlock-ada` PR #134.
  * Import verified evidence from `evidence/REA-CPU-01/`.
  * Create `HISTORY_ADA_D3COLD.md` and `HISTORY_REA_CPU_01.md`.
* **Exit Criteria:** All historical claims map directly to SHA-256 verified files with zero gaps filled by hallucinated narrative.

### Phase 1: Intellectual Replication of the D3cold Trap
* **Goal:** Extract the universal anti-fake lesson from the Ada investigation.
* **Tasks:**
  * Document the divergence between GSP-RM software getters and physical BAR0 register states.
  * Construct a synthetic fixture (`fixtures/ada_d3cold_false_positive`) where software reports `unlocked` while benchmark reports `stock`.
* **Exit Criteria:** The pipeline verifier correctly rejects the false positive and issues `REFUTED`.

### Phase 2: Ingestion of REA-CPU-01 Case Study
* **Goal:** Demonstrate transfer of the protocol to an entirely different architecture (Zen 4 Hawk Point).
* **Tasks:**
  * Document the 9-run counterbalanced power profile matrix.
  * Document the `NO_SAFE_WRITE_PATH_IDENTIFIED` SMU milestone.
  * Document the XDNA NPU kernel vs userspace discovery.
* **Exit Criteria:** Protocol demonstrates how to record negative results as valid scientific milestones.

### Phase 3: Protocol Specification & JSON Schemas
* **Goal:** Formalize machine-readable data structures for experiments and evidence.
* **Tasks:**
  * Draft `schemas/experiment.schema.json`.
  * Draft `schemas/evidence.schema.json`.
  * Specify `docs/COMMON_RESEARCH_PIPELINE.md` and `docs/SAFETY_CONTRACTS.md`.
* **Exit Criteria:** Schemas validate experiment plans with explicit pre-conditions, controls, and rollback hooks.

### Phase 4: Core Engine Implementation (Read-Only / Report-First)
* **Goal:** Build the decoupled Python engine in `core/`.
* **Tasks:**
  * Implement `core/discovery.py` (Sysfs/Proc reader).
  * Implement `core/hypothesis.py` (Ledger manager).
  * Implement `core/sentinel.py` (Risk evaluation and authorization).
  * Implement `core/executor.py` (Whitelist execution runner with thermal monitoring).
  * Implement `core/rollback.py` (Independent state restoration).
  * Implement `core/verifier.py` (Mathematical parity and anti-fake logic).
  * Implement `core/recorder.py` (Evidence bundler and privacy sanitizer).
* **Exit Criteria:** Engine runs end-to-end in mock mode without writing to real hardware.

### Phase 5: Modular Hardware Adapters
* **Goal:** Build modular adapters for diverse hardware subsystems in `adapters/`.
* **Tasks:**
  * Implement `adapters/base.py` with typed `HardwareAdapter` interface.
  * Implement `adapters/linux_inventory.py` (CPU, memory, sysfs).
  * Implement `adapters/linux_cpu_perf.py` (`powerprofilesctl` / governors).
  * Implement `adapters/nvidia_telemetry.py` (NVML / `nvidia-smi`).
  * Implement `adapters/amd_xdna_discovery.py` (`/dev/accel/accel0`).
* **Exit Criteria:** All adapters gracefully emit `CapabilityStatus.NOT_APPLICABLE` when target hardware is missing.

### Phase 6: Anti-Fake Test Suite & Adversarial Fixtures
* **Goal:** Prove the pipeline detects false positives, mathematical drift, and unmanaged confounders.
* **Tasks:**
  * Create synthetic test cases for all 12 adversarial scenarios (`docs/ANTIFAKE_TEST_MATRIX.md`).
  * Implement `tests/test_antifake_sentinel.py`, `tests/test_d3cold_trap_detection.py`, `tests/test_rollback_controller.py`.
* **Exit Criteria:** 100% of adversarial test cases pass (dangerous actions rejected, false speedups caught).

### Phase 7: Multi-Machine Portability Validation
* **Goal:** Validate the protocol on a second physical machine or virtualized testbed.
* **Tasks:**
  * Run discovery and read-only baseline on an alternate architecture (e.g. Intel or ARM64).
  * Verify that lack of Ada GPU or AMD APU does not cause crashes.
* **Exit Criteria:** Portable report generated cleanly without target leakage.

### Phase 8: Public Repository Proposal & Licensing
* **Goal:** Prepare the project for open-source publication upon user approval.
* **Tasks:**
  * Recommend license (e.g., Apache 2.0 or MIT).
  * Perform complete privacy audit (zero personal tokens, IPs, MAC addresses, or personal usernames).
  * Request explicit human authorization from Danny before publishing.
* **Exit Criteria:** Repository is self-contained, documented, clean, and ready for publication.

---

## 3. Status Against the Code on Disk (verified 2026-11-06)

Sections 1 and 2 above are the plan **as written before implementation**. They are kept unedited.
This table says which exit criteria the repository actually meets today, because two of them are
contradicted by the evidence and one is already superseded. Nothing here should be read as
retroactively grading a phase the plan never reached.

| Phase | Status today | What was checked |
| :--- | :--- | :--- |
| **0** Ground truth | Done | `docs/HISTORY_ADA_D3COLD.md` and `docs/HISTORY_REA_CPU_01.md` exist and are the referenced case studies. The "zero gaps filled by hallucinated narrative" criterion is a claim about prose quality and is not mechanically re-checked here. |
| **1** D3cold replication | Done | `fixtures/ada_d3cold_false_positive/` exists and `tests/test_d3cold_trap_detection.py` asserts the verifier issues `REFUTED`. |
| **2** REA-CPU-01 ingestion | Done | `docs/HISTORY_REA_CPU_01.md` records the 9-run counterbalanced matrix, `NO_SAFE_WRITE_PATH_IDENTIFIED` and the XDNA kernel-versus-userspace result. |
| **3** Schemas | Done, narrower than the wording | Both `schemas/experiment.schema.json` and `schemas/evidence.schema.json` exist, but **no runtime code loads them**. `core/sentinel.py` carries a stdlib validator whose required-key set is asserted equal to the schema's by `tests/test_sentinel_plan_validation.py`, and the comment above `EXPERIMENT_PLAN_REQUIRED` states the intent: the schema stays declarative documentation rather than a runtime dependency. Nothing in `core/` or `tests/` imports `jsonschema`, and the repository declares no third-party dependency manifest at all, so whether a plan is enforceable never depends on what a given machine happens to have installed. |
| **4** Core engine | Partial | `discovery.py`, `sentinel.py`, `executor.py`, `rollback.py` and `verifier.py` exist, plus eight modules the plan did not name (`authorization.py`, `cli.py`, `cpu_power.py`, `experiment.py`, `planning.py`, `recovery.py`, `tier1.py`, `workload_runner.py`). **`core/hypothesis.py` and `core/recorder.py` were never created.** The parenthetical "with thermal monitoring" is false for `core/executor.py`: it contains no sensor reading and no pressure abort, which [ANTIFAKE_TEST_MATRIX](ANTIFAKE_TEST_MATRIX.md) records as `AF-10 = NOT_DEMONSTRATED`. The mock-mode exit criterion is met by `DryRunExecutor` (`core/executor.py:37`). |
| **5** Adapters | Done, different file layout | The four detectors exist as classes inside `adapters/adapters.py` — `LinuxInventoryAdapter`, `LinuxCpuPerfAdapter`, `NvidiaTelemetryAdapter`, `AmdXdnaDiscoveryAdapter` — not as one file each. `adapters/base.py` defines `HardwareAdapter` and `CapabilityStatus`, and `CapabilityStatus.NOT_APPLICABLE` is emitted by the probes when the target is absent. `ada_adapter.py` and `rea_adapter.py` carry the two case-study probes. |
| **6** Anti-fake suite | **Exit criterion not met** | All three named test files exist, but "100% of adversarial test cases pass" is contradicted by the status table in [ANTIFAKE_TEST_MATRIX](ANTIFAKE_TEST_MATRIX.md): `AF-02` (per-GHz normalization), `AF-09` (automatic restoration on the next launch), `AF-10` (`RESOURCE_PRESSURE_ABORT`) and `AF-12` are `NOT_DEMONSTRATED`. The suite passes; the matrix it is supposed to close does not. |
| **7** Multi-machine portability | `NOT_DEMONSTRATED` | No run on a second architecture is recorded anywhere in this repository. [PORTABILITY_MATRIX](PORTABILITY_MATRIX.md) is a design matrix of interfaces and privileges, not evidence: it carries no dated run, host or digest. Everything executed here ran on one AMD Zen 4 mobile host with an NVIDIA Ada GPU, plus the Linux/Bubblewrap sandbox tests. |
| **8** Public repository | Superseded — it is published | `LICENSE` is MIT. The repository is public at `DannyBaanks/hardware-research-agent`, so "request authorization before publishing" is history, and `ROADMAP.md` carries the matching `[HISTORICAL]` note. The privacy check is **not** a sealed one-time audit: it is an obligation on every commit (`AGENTS.md` §2 — zero MACs, board serials or tokens in staged files). |

**What closed the safety gap the plan assumed.** Phases 3–6 as written left authorization lifetime and
rollback coupling unspecified. That work is IHRA-04, tracked in
[`plans/ihra-autonomy-safety-prerequisites.md`](../plans/ihra-autonomy-safety-prerequisites.md): grant
expiry with digest binding, a statically verifiable `rollback_plan`, execution-coupled rollback evidence
with observed status, and the human-present Tier-1 path with a blocking emergency fault state. What it
explicitly did **not** close is listed in [SAFETY_CONTRACTS §6.5](SAFETY_CONTRACTS.md).

**Next real work:** Phase 4's missing modules (`hypothesis.py`, `recorder.py` with the privacy
sanitizer), Phase 6's four open `AF` rows, and Phase 7 on a second machine.
