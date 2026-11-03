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
