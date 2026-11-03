# ISyCo Hardware Research Agent (IHRA)
## A Portable, Safety-Hardened Protocol for Reproducible Hardware Research on Linux

[![Tests](https://img.shields.io/badge/tests-22%20passed-brightgreen.svg)]()
[![License Proposal](https://img.shields.io/badge/license-Apache%202.0%20(proposed)-blue.svg)]()
[![Status](https://img.shields.io/badge/status-PROPOSAL%20%2F%20HRA--00-yellow.svg)]()

> *"It is not a magic optimizer. It is a reproducible, supervised research protocol capable of concluding that NO safe optimization exists."*

---

## 1. Overview

The **ISyCo Hardware Research Agent (IHRA)** formalizes the experimental methodology born from two ground-truth hardware campaigns:
1. **Case A (NVIDIA Ada Lovelace AD107M / PR #134):** The discovery of the **Optimus D3cold Power-Rail Trap**, proving how software getters (`0 0 0`) report false-positive unlocked states while physical execution runs at stock rate until D3cold is prevented.
2. **Case B (AMD Ryzen 5 8645HS / REA-CPU-01):** The multi-run counterbalanced power profile matrix (+12.6% clock uplift in `performance`), the discovery of the unconfigured 16 TOPS XDNA NPU (`/dev/accel/accel0`), and the safe prevention of bricking via `NO_SAFE_WRITE_PATH_IDENTIFIED` on the SMU.

IHRA extracts these lessons into an **open, decoupled, and portable agent protocol** that can be safely handed to any AI coding assistant on a Linux system.

---

## 2. Core Principles & Safety Invariants

* **Read-Only by Default:** Zero configuration changes without explicit, human-granted session permissions.
* **Separation of Duties:**
  * **Proposer (Agent):** Formulates hypotheses and drafts plans.
  * **Sentinel (Gatekeeper):** Evaluates risk tiers (0 to 3) and issues `AUTHORIZE` or `DENY`.
  * **Executor (Engine):** Hardened whitelist runner with continuous thermal and memory monitoring.
* **Mandatory Automated Rollback:** No state modification may occur without a verified rollback query that confirms post-test restoration.
* **The Triangulation Mandate:** Software telemetry alone is rejected. Hardware readbacks, physical execution throughput, and mathematical output parity (SHA-256) must all agree.

---

## 2b. The Tri-Repo Ecosystem & Zero-Bloat Bridge

Rather than forcing LLM agents to run heavy background MCP servers (which advertise 3.5 MB of schemas and consume hundreds of megabytes of RAM idle) or cumbersome terminal TUIs, the **IHRA Unified CLI Bridge** connects three specialized open-source tools via clean Unix JSON pipelines:

| Repository | Role | Upstream / Origin | Key Capability |
|---|---|---|---|
| [DannyBaanks/rea](https://github.com/DannyBaanks/rea) | Binary & Firmware Forensics | Fork of `morluto/rea` (Tag 6.5.0) | Disassembly, bounded tracing, compact 7 KB profile (`REA_MCP_TOOL_PROFILE=compact`), and Malbolge reverse-engineering suite. |
| [DannyBaanks/gpu-unlock-ada](https://github.com/DannyBaanks/gpu-unlock-ada) | GPU Capability Unlock & Canary | Fork of `JustAResearcher/gpu-unlock-ada` | PR #134 canary, 2.00x GEMM multiplier, and `DynamicPowerManagement=0x00` D3cold rail persistence. |
| [DannyBaanks/hardware-research-agent](https://github.com/DannyBaanks/hardware-research-agent) | Research Protocol & Safety Sentinel | Canonical protocol repo | Decoupled Sentinel Gate, automated rollback, antifake delta verifier, and SHA-256 evidence bundles. |

### Quickstart: Single-Command Agent Interface

```bash
# Direct Node/npm runner (or via npx)
./bin/ihra.mjs discover

# Check hardware state & companion repositories
python3 -m core.cli setup

# Run pre-flight safety check on a proposed experiment command
python3 -m core.cli sentinel "wrmsr -a 0x199 0x2000"   # -> DENIED (Exit 1)
python3 -m core.cli sentinel "python3 benchmark.py"     # -> AUTHORIZED (Exit 0)

# Query Ada GPU telemetry and D3cold rail safety
python3 -m core.cli ada

# Run REA binary analysis without starting an MCP server
python3 -m core.cli inspect /path/to/binary --tool inspect_malbolge
```

---

## 3. Repository Structure

```text
hardware-research-agent/
├── README.md                          # Repository overview and quickstart
├── ROADMAP.md                         # Master roadmap and activation gates
├── AGENTS.md                          # Behavioral and safety contract for AI agents
├── docs/
│   ├── HISTORY_ADA_D3COLD.md          # Case study: The Optimus D3cold power-rail trap
│   ├── HISTORY_REA_CPU_01.md          # Case study: REA-CPU-01 Hawk Point forensics
│   ├── COMMON_RESEARCH_PIPELINE.md    # The universal 10-stage research protocol
│   ├── ARCHITECTURE.md                # System design, data flow, and state machines
│   ├── SAFETY_CONTRACTS.md            # Risk tiers, forbidden actions, and threat model
│   ├── ANTIFAKE_TEST_MATRIX.md        # The 12 mandatory adversarial failure scenarios
│   ├── PORTABILITY_MATRIX.md          # Cross-platform compatibility (AMD/Intel/ARM64)
│   └── IMPLEMENTATION_PLAN.md         # Phased implementation roadmap (Phase 0 to 8)
├── schemas/
│   ├── experiment.schema.json         # JSON schema for experiment plans and rollback hooks
│   └── evidence.schema.json           # JSON schema for cryptographic evidence bundles
├── adapters/
│   ├── base.py                        # Typed HardwareAdapter base class and CapabilityStatus
│   └── adapters.py                    # Linux sysfs, CPU perf, NVIDIA, and AMD XDNA adapters
├── core/
│   ├── discovery.py                   # Non-invasive hardware inventory collector
│   ├── sentinel.py                    # Safety gatekeeper and risk tier evaluator
│   ├── verifier.py                    # Anti-fake triangulation and math parity verifier
│   └── rollback.py                    # Independent state restoration and verification engine
├── fixtures/
│   ├── ada_d3cold_false_positive/     # Synthetic fixture reproducing the D3cold software trap
│   └── no_safe_gain_found/            # Synthetic fixture demonstrating negative result handling
└── tests/
    ├── test_d3cold_trap_detection.py  # Verifies rejection of false-positive software getters
    ├── test_antifake_sentinel.py      # Verifies blocking of Tier 3 and unauthorized actions
    ├── test_evidence_verifier.py      # Verifies math corruption and negative control checks
    ├── test_rollback_controller.py    # Verifies state restoration and mismatch detection
    └── test_adapters_readonly.py      # Verifies graceful degradation on missing hardware
```

---

## 4. Quickstart & Verification

Run the complete test suite:
```bash
python3 -m unittest discover -s tests -v
```

Expected output:
```text
Ran 16 tests in 0.120s

OK
```

---

## 5. Proposed License & Publication Status

* **Status:** `PROPOSAL / HRA-00` (Local implementation in `/home/danny/Development/ISyCo Git/hardware-research-agent/`).
* **Recommended License:** Apache 2.0 (permissive with explicit patent grants and liability limitation).
* **Publication Policy:** Requires explicit human authorization before creating public remotes or sharing outside the ISyCo research tree.
