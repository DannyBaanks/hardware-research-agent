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
| [Sentinel](core/sentinel.py) | Derives risk from the command and accepts only exact, allowlisted read-only commands | Denies all state changes; not an execution sandbox, authenticated approval system, or executor |
| [AntiFake verifier](core/verifier.py) | Requires a valid SHA-256 per run, finite throughput values, an explicit negative-control delta, and checks contradictory unlock claims | Evidence is caller-supplied; hashes are compared, not recomputed from raw outputs; no physical readback or formal significance test |
| [Rollback controller](core/rollback.py) | Restricts execution to a power-profile restore/query pair without a shell | **Separate helper**, not wired to an experiment runner; does not establish an end-to-end rollback guarantee |
| [Dry-run executor](core/executor.py) | Replays two bundled synthetic evidence fixtures through the verifier | **No workload execution, hardware access, or system mutation** |
| [Synthetic workload executor](core/executor.py) | Runs only the pinned `sha256-chain-v1` CPU workload in a rootless Bubblewrap sandbox with read-only host mounts and isolated temporary filesystems | Not a benchmark or experiment executor; no caller-provided commands, hardware telemetry/control, or persistent host writes |
| [Ada adapter](adapters/ada_adapter.py) | Detects selected NVIDIA PCI IDs, reads the driver's power-management parameter and optional `nvidia-smi` telemetry | A driver setting is **not proof** that the hardware unlock is active or safe |
| [REA adapter](adapters/rea_adapter.py) | Locates a local REA runner or a Node/npx route and can invoke a requested analysis tool | This **executes external tooling**; it is not a sandbox and may invoke npm tooling |
| Node wrapper | [bin/ihra.mjs](bin/ihra.mjs) dispatches to the Python CLI | Node is a convenience wrapper; Python does the work |

The [architecture specification](docs/ARCHITECTURE.md) describes a future autonomous execution owner, hypothesis ledger and evidence recorder; those production modules are **not implemented**. `DryRunExecutor` only replays fixed local fixture data and never executes a workload. Protocol specifications and schemas document intended controls; their existence alone does not enforce them.

## Start safely (Linux)

**Requirements:** Python 3 with the standard library. The fixed workload command additionally requires root-owned `/usr/bin/bwrap` and `/usr/bin/python3`, with rootless user namespaces enabled. Node.js is optional for the JavaScript wrapper. Certain inspections use installed system utilities or companion software.

```bash
git clone https://github.com/DannyBaanks/hardware-research-agent.git
cd hardware-research-agent

# Inspect the implemented CLI and run the host-side tests
python3 -m core.cli --help
python3 -m unittest discover -s tests -v

# Discovery: inventory and optional read-only telemetry queries
python3 -m core.cli discover

# Replay synthetic evidence (does not execute a workload)
python3 -m core.cli dry-run ada_d3cold_false_positive

# Run the one fixed CPU workload in a rootless sandbox
python3 -m core.cli run-synthetic sha256-chain-v1

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
| `python3 -m core.cli ada` | Reads NVIDIA state and its driver power-management parameter | Reports `NOT_DEMONSTRATED`; a configured parameter is not proof of persistent hardware state or unlock safety |
| `python3 -m core.cli sentinel 'powerprofilesctl get'` | Checks an exact read-only command against the allowlist | Classification only; no command is executed. All state-changing and unlisted commands are denied |
| `python3 -m core.cli dry-run <fixture>` | Replays one of two checked-in synthetic evidence fixtures through the verifier | Uses fixture data only; performs zero workload commands, hardware reads, writes, or rollback actions |
| `python3 -m core.cli run-synthetic sha256-chain-v1` | Runs a fixed 20,000-step SHA-256 chain in a rootless Bubblewrap sandbox | Requires trusted Bubblewrap and system Python; does not accept commands or scripts, access hardware telemetry/control, or persist host state |
| `python3 -m core.cli inspect /path/to/file --tool inspect_malbolge` | Delegates analysis to REA | May start a local Node tool or `npx`; inspect dependencies before running |

The Sentinel no longer accepts `--human-approved` or a caller-provided tier as authority. A read-only allowlist verdict still **does not grant permission to execute a command**. The synthetic executor is a narrow, fixed-workload sandbox; it is not connected to Sentinel, hardware adapters, experiment plans, or rollback. No authenticated write approval exists.

## Protocol and evidence

The [Common Hardware Research Pipeline](docs/COMMON_RESEARCH_PIPELINE.md) describes the **target workflow**, not a fully implemented unattended service:

```text
DISCOVER → BASELINE → HYPOTHESIZE → CONTROL → RISK REVIEW
  → AUTHORIZE → EXPERIMENT → VERIFY → RESTORE → EVIDENCE / RETURN
```

Before making a performance claim, the methodology calls for a comparable baseline, controls, repeatable workloads, numerical correctness checks, independent physical evidence where available, and a recorded restoration result. Outcomes such as `REFUTED`, `NOT_DEMONSTRATED`, `NO_SAFE_GAIN_FOUND` and `DENY` must be reported without disguising them as success.

### Current antifake implementation — important gaps

- The verifier requires **at least three runs per side**, valid SHA-256-formatted output hashes for **every** run, finite positive throughput values, and an explicit negative-control delta; it computes a **median throughput ratio**. This is not a formal significance test, and the control evidence remains caller-supplied.
- Hash comparison detects divergence in the **supplied** digests; it does not recalculate them from raw output artifacts.
- The special D3cold rule rejects a software-unlocked claim when measured throughput stays near stock; the verifier does **not itself read BAR0** or prove the cause of the mismatch.
- The Sentinel now derives the tier and allows only exact, no-argument read-only commands; all other commands, including arbitrary benchmarks, are denied. This is a conservative classifier, not an OS-enforced sandbox.
- Tier 1/2 changes remain disabled because IHRA has no authenticated human-approval mechanism or experiment executor coupled to mandatory restoration. The separate rollback helper supports only a constrained power-profile restore/query pair.

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

The repository contains `unittest` tests for mocked adapters, Sentinel allowlist denials, constrained rollback behavior, verifier input bounds, required hashes, fixture replay, and the fixed synthetic workload executor. The executor test suite checks its fixed workload contract, expected digest, fail-closed behavior, and resource-limit outcomes; a successful sandbox run is not evidence of hardware performance or an autonomous experiment lifecycle. There is no physical end-to-end hardware validation or GitHub Actions workflow under `.github/workflows/` in the inspected tree.

Consult [fixtures](fixtures/), [experiment schema](schemas/experiment.schema.json) and [evidence schema](schemas/evidence.schema.json) for the current test contracts. Synthetic fixtures are not raw hardware execution logs.

## Safety and publication

- Read-only research is the default **policy goal**, not a claim of universal OS-level isolation.
- Tier 3 operations such as direct firmware, SMU or MSR writes are forbidden by the documented protocol; the current string-based gate is **not a substitute** for an OS-enforced deny-by-default executor.
- No automatic overclocking, undervolting, BIOS modification or thermal-limit bypass is provided or endorsed.
- Never publish hostnames, serials, GPU UUIDs, tokens or raw private traces without a separate redaction review.

**License: MIT.** The repository root contains the MIT grant in [`LICENSE`](LICENSE), and `package.json` declares the same identifier. DannyBaanks selected MIT; the earlier Apache-2.0 proposal is superseded.

**Status:** publicly available **HRA-00 research prototype**. See [ROADMAP.md](ROADMAP.md) for the original staged proposal; older notes referring to a not-yet-created public repository are historical, not the current publication state.
