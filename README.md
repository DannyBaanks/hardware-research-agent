# ISyCo Hardware Research Agent (IHRA)

**A research-first protocol for reproducible, supervised Linux hardware experiments.**

[Español](README_es.md) · [Research pipeline](docs/COMMON_RESEARCH_PIPELINE.md) · [Safety contracts](docs/SAFETY_CONTRACTS.md) · [Roadmap](ROADMAP.md) · [Case studies](#research-origins)

> [!IMPORTANT]
> **Public research prototype (HRA-00), not a production-safe autonomous optimizer.** IHRA helps discover capabilities, assess proposed operations, inspect selected telemetry, and evaluate benchmark evidence. It does **not** currently enforce an end-to-end, sandboxed experiment/authorization/rollback lifecycle. Do not give it privileged access on the assumption that its policy documents are implemented.

**Guiding principle:** A well-supported conclusion of **no safe improvement found** is a valid scientific result. Nothing here promises more FPS, more tokens per second, or a universal GPU/CPU unlock.

## What is actually implemented

| Component | Present behavior | Boundary |
|---|---|---|
| Hardware discovery | Reads CPU features, platform profiles, selected PCI/NPU signals and optional NVIDIA telemetry | Some hardware assumptions and fixed paths are platform-specific; not a full portable inventory |
| [Sentinel](core/sentinel.py) | Evaluates a supplied command string and caller-specified risk tier; rejects several dangerous patterns and tier 3 | **Advisory regex classifier**, not an execution sandbox or proof of human authorization |
| [AntiFake verifier](core/verifier.py) | Compares median throughput, supplied hashes, negative-control delta and contradictory unlock claims | Requires caller-supplied data; does not independently collect physical readbacks or certify statistical significance |
| [Rollback controller](core/rollback.py) | Runs an explicitly provided restoration command, then compares a verification result | **Separate helper**, not wired to an automatic experiment runner; currently uses shell execution |
| [Ada adapter](adapters/ada_adapter.py) | Detects selected NVIDIA PCI IDs, reads the driver's power-management parameter and optional `nvidia-smi` telemetry | A driver setting is **not proof** that the hardware unlock is active or safe |
| [REA adapter](adapters/rea_adapter.py) | Locates a local REA runner or a Node/npx route and can invoke a requested analysis tool | This **executes external tooling**; it is not a sandbox and may invoke npm tooling |
| Node wrapper | [bin/ihra.mjs](bin/ihra.mjs) dispatches to the Python CLI | Node is a convenience wrapper; Python does the work |

Modules proposed in the [architecture specification](docs/ARCHITECTURE.md), including an independent executor, hypothesis ledger and evidence recorder, are **not all present in `core/`**. Protocol specifications and schemas document intended controls; their existence alone does not enforce them.

## Start safely (Linux)

**Requirements:** Python 3 with the standard library; Node.js is optional for the JavaScript wrapper. Certain inspections use installed system utilities or companion software.

```bash
git clone https://github.com/DannyBaanks/hardware-research-agent.git
cd hardware-research-agent

# Inspect the implemented CLI and run the host-side tests
python3 -m core.cli --help
python3 -m unittest discover -s tests -v

# Discovery: inventory and optional read-only telemetry queries
python3 -m core.cli discover

# Equivalent Node wrapper (requires Node.js)
node bin/ihra.mjs discover
```

> [!NOTE]
> Discovery does not intentionally change persistent settings, but tools such as `nvidia-smi` can wake or initialize a dGPU on some systems. The discovery output is a **capability hint**, not independent validation of hardware performance. Review the source before using the agent on a sensitive machine.

### CLI commands and their limits

| Command | What it does | Important caveat |
|---|---|---|
| `python3 -m core.cli discover` | Prints inventory and adapter results | Some GPU/NPU detections rely on a fixed device list or path |
| `python3 -m core.cli setup` | Reports companion repository locations | Currently checks **hard-coded developer-directory paths**; not a general installation checker |
| `python3 -m core.cli ada` | Reads NVIDIA state and an inferred D3cold risk | Its current recommendation text may **overstate** unlock safety or GEMM gain; verify physically |
| `python3 -m core.cli sentinel 'wrmsr 0x199 0x2000'` | Demonstrates denial of a forbidden command string | This is **classification only**: the string is not executed |
| `python3 -m core.cli inspect /path/to/file --tool inspect_malbolge` | Delegates analysis to REA | May start a local Node tool or `npx`; inspect dependencies before running |

**Do not use `--human-approved` as an authentication mechanism.** It is only a boolean argument supplied by the caller. Likewise, a Sentinel `AUTHORIZED` result **does not grant permission to run that command** and does not automatically trigger a safe executor or verified rollback.

## Protocol and evidence

The [Common Hardware Research Pipeline](docs/COMMON_RESEARCH_PIPELINE.md) describes the **target workflow**, not a fully implemented unattended service:

```text
DISCOVER → BASELINE → HYPOTHESIZE → CONTROL → RISK REVIEW
  → AUTHORIZE → EXPERIMENT → VERIFY → RESTORE → EVIDENCE / RETURN
```

Before making a performance claim, the methodology calls for a comparable baseline, controls, repeatable workloads, numerical correctness checks, independent physical evidence where available, and a recorded restoration result. Outcomes such as `REFUTED`, `NOT_DEMONSTRATED`, `NO_SAFE_GAIN_FOUND` and `DENY` must be reported without disguising them as success.

### Current antifake implementation — important gaps

- The verifier requires **at least three runs per side** and computes a **median throughput ratio**. This is not a formal significance test; fixed confidence values should not be presented as statistical confidence intervals.
- The output-hash comparison detects divergent **supplied** hashes, but currently does **not require a nonempty hash in every run**. Missing hashes can therefore escape this check.
- A negative-control delta defaults to zero if omitted. Zero supplied by default is **not evidence** that a real negative control was run.
- The special D3cold rule rejects a software-unlocked claim when measured throughput stays near stock; the verifier does **not itself read BAR0** or prove the cause of the mismatch.
- The Sentinel's command-pattern checks and caller-provided tier are **not sufficient** for adversarial command authorization. Do not use them as a security boundary for arbitrary generated shell commands.
- Rollback is not automatically coupled to the CLI's Sentinel verdict. The current controller calls commands via `shell=True`; restrict it to reviewed inputs in a controlled environment.

See [Safety contracts](docs/SAFETY_CONTRACTS.md), [12 proposed adversarial scenarios](docs/ANTIFAKE_TEST_MATRIX.md), and the [implementation plan](docs/IMPLEMENTATION_PLAN.md) for the stronger design intended by the project.

## Research origins

These are **source case studies**, not optimizations performed automatically by IHRA:

**A. Optimus `D3cold` / RTX 4050 Laptop.** A boot-time Ada unlock appeared successful to the driver, but a subsequent power-state transition reset volatile hardware registers while the software getter still reported full rate. The decisive control compared physical BAR0 state and actual instruction throughput. On one measured laptop, the retained unlock approached **2× in a tensor instruction probe**; cuBLAS GEMM improved by smaller workload-dependent amounts. See the [IHRA case study](docs/HISTORY_ADA_D3COLD.md), [original evidence](https://github.com/DannyBaanks/gpu-unlock-ada/tree/main/docs/evidence/ada28a1), and [upstream PR #134](https://github.com/JustAResearcher/gpu-unlock-ada/pull/134).

**B. REA-CPU-01 / Ryzen 5 8645HS.** A counterbalanced power-profile campaign reported approximately **+12.6% sustained clock** but **+5.3% in a particular CPU microbenchmark** on one HP Victus. The XDNA device was detected, but useful user-space NPU inference was not established; no supported safe write path for the explored SMU controls was identified. See the [CPU case study](docs/HISTORY_REA_CPU_01.md). This is **not** a universal CPU boost.

### Related repositories

- [REA — reverse-engineering tools](https://github.com/DannyBaanks/rea): companion research tooling with its own prerequisites and security considerations.
- [gpu-unlock-ada — experimental GPU work](https://github.com/DannyBaanks/gpu-unlock-ada): separate kernel/firmware research; **not installed or run automatically by IHRA**.

## Tests and reproducibility

```bash
python3 -m unittest discover -s tests -v
```

The repository contains **22 `unittest` test methods** across six files (counted from the checked-in test sources). They exercise mocked adapters, regex denials, rollback-result checks, hashes and synthetic D3cold failures. **This README does not claim an independently observed passing CI run** or physical end-to-end hardware validation. There is currently no GitHub Actions workflow under `.github/workflows/` in the inspected tree.

Consult [fixtures](fixtures/), [experiment schema](schemas/experiment.schema.json) and [evidence schema](schemas/evidence.schema.json) for the current test contracts. Synthetic fixtures are not raw hardware execution logs.

## Safety and publication

- Read-only research is the default **policy goal**, not a claim of universal OS-level isolation.
- Tier 3 operations such as direct firmware, SMU or MSR writes are forbidden by the documented protocol; the current string-based gate is **not a substitute** for an OS-enforced deny-by-default executor.
- No automatic overclocking, undervolting, BIOS modification or thermal-limit bypass is provided or endorsed.
- Never publish hostnames, serials, GPU UUIDs, tokens or raw private traces without a separate redaction review.

**License status needs a repository decision:** `package.json` currently declares `MIT`, while earlier documentation proposed `Apache-2.0`, and the inspected repository root has **no `LICENSE` file**. This README does not assert that a specific license grant is finalized. Choose a license, add its actual license text, and align package metadata and documentation before a release.

**Status:** publicly available **HRA-00 research prototype**. See [ROADMAP.md](ROADMAP.md) for the original staged proposal; older notes referring to a not-yet-created public repository are historical, not the current publication state.
