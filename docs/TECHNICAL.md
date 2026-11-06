# IHRA — Technical reference

[← Back to the README](../README.md) · [Español](TECHNICAL_es.md)

This page holds the precise, component-by-component description of what IHRA implements and where its guarantees stop. It was the body of the README until 2026-11-03.

## What is actually implemented

| Component | Present behavior | Boundary |
|---|---|---|
| Hardware discovery | Reads CPU features, platform profiles, selected PCI/NPU signals and optional NVIDIA telemetry | Some hardware assumptions and fixed paths are platform-specific; not a full portable inventory |
| [Sentinel](../core/sentinel.py) | Derives risk from the command and accepts only exact, allowlisted read-only commands; `evaluate_tier1()` separately classifies one consented profile change | `evaluate_plan()` — the path any unattended caller takes — denies every state change. Not an execution sandbox or authenticated approval system; the Tier-1 path exists only for a person typing at this TTY |
| [AntiFake verifier](../core/verifier.py) | Requires a valid SHA-256 per run, finite throughput values, an explicit negative-control delta, and checks contradictory unlock claims | Evidence is caller-supplied; hashes are compared, not recomputed from raw outputs; no physical readback or formal significance test |
| [Rollback controller](../core/rollback.py) | `RollbackController` restricts execution to one power-profile restore/query pair without a shell; `RollbackContract` implements `capture_prestate` / `execute_rollback` / `verify_restoration` over the six monitored CPU power nodes | It is wired into both runners and the status is derived from re-reads, but restoration is verified by comparing observations, not by attesting them: receipts are hashed into the manifest, never signed |
| [Tier-1 profile runner](../core/tier1.py) | Applies exactly one `powerprofilesctl set <profile>` change, only after a live one-use TTY confirmation bound to the plan digest **and** the digest of the intervention bytes, then proves the host back to the captured prestate | The only IHRA path that writes persistent host state, and it is consent-gated, not autonomous. An unverified restoration seals an emergency fault and blocks later campaigns; nothing restores the host by itself |
| [Campaign inspector and fault state](../core/recovery.py) | Re-reads sealed campaign evidence against its manifest, re-derives the rollback status instead of trusting the stored label, and lists or resolves emergency fault records read-only | It reports; it does not repair. Resolving a fault requires a human to have put the host back, and there is no boot-time supervisor |
| [Dry-run executor](../core/executor.py) | Replays two bundled synthetic evidence fixtures through the verifier | **No workload execution, hardware access, or system mutation** |
| [Synthetic workload executor](../core/executor.py) | Runs only the pinned `sha256-chain-v1` CPU workload in a rootless Bubblewrap sandbox with read-only host mounts and isolated temporary filesystems | Not a benchmark or experiment executor; no caller-provided commands, hardware telemetry/control, or persistent host writes |
| [Synthetic experiment runner](../core/experiment.py) | Executes the fixed five-sample plan only after a one-use local TTY confirmation bound to the proposal and plan digests; re-validates the grant's monotonic expiry before every sample, and seals `prestate.json`, `poststate.json` and `rollback.json` with the campaign evidence | TTY consent is not authenticated identity and does not protect against malicious same-UID processes; no general planning or hardware authority. A campaign without coherent rollback evidence is `INCOMPLETE`, not a pass |
| [Ada adapter](../adapters/ada_adapter.py) | Matches the NVIDIA display function against the exact Ada IDs the gpu-unlock-ada module knows, reads `DynamicPowerManagement` from `/proc/driver/nvidia/params`, the kernel-reported PCI power state, the `gpu_unlock` module's parameters, and optional `nvidia-smi` telemetry | All software-reported: a driver setting or a loaded module is **not proof** that the hardware unlock is active or safe |
| [REA adapter](../adapters/rea_adapter.py) | Locates a local REA clone (run with `node`) or an installed `rea` CLI and runs one REA CLI command (default `inspect-artifact`) | This **executes external tooling** and is not a sandbox. It never falls back to `npx` (no package downloads); MCP-only tools such as `inspect_malbolge` are rejected |
| Node wrapper | [bin/ihra.mjs](../bin/ihra.mjs) dispatches to the Python CLI | Node is a convenience wrapper; Python does the work |

The [architecture specification](ARCHITECTURE.md) describes a future autonomous execution owner, hypothesis ledger and evidence recorder; those production modules are **not implemented**. `DryRunExecutor` only replays fixed local fixture data and never executes a workload. Protocol specifications and schemas document intended controls; their existence alone does not enforce them.

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

# Inspect the fixed plan and export its strict, non-executable proposal
python3 -m core.cli show-synthetic-plan
python3 -m core.cli show-synthetic-proposal > /tmp/ihra-proposal.json
python3 -m core.cli review-synthetic-proposal /tmp/ihra-proposal.json

# Review the exact proposal again and authorize interactively in the controlling TTY
python3 -m core.cli run-synthetic-experiment --proposal /tmp/ihra-proposal.json

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
| `python3 -m core.cli show-synthetic-proposal` | Emits the strict JSON proposal for the only eligible synthetic plan | Proposal output is inert data and grants no execution authority |
| `python3 -m core.cli review-synthetic-proposal <file>` | Validates and displays a proposal without executing or authorizing it | Unknown fields, commands, workloads, and changed limits are rejected |
| `python3 -m core.cli run-synthetic-experiment --proposal <file>` | Displays the exact proposal, requires typing `AUTHORIZE <plan-sha256>` in the controlling TTY, then runs five sequential samples | No TTY, EOF, cancellation, or digest mismatch denies execution; TTY confirmation is not cryptographic identity and does not protect against same-UID processes |
| `python3 -m core.cli inspect-synthetic-campaign <campaign-id>` | Reads campaign evidence and checks manifest, plan, proposal, checkpoint, sample receipts and the rollback triple | Read-only; does not resume or execute. It re-derives the rollback status from the sealed states. Historical campaigns lacking the current proposal/checkpoint contract remain `NOT_DEMONSTRATED` |
| `python3 -m core.cli run-tier1 --set <profile>` | Builds the plan for one profile change, reads the way back from this host, asks you to type `AUTHORIZE <plan-sha256>` at this TTY, applies the change, measures, restores and re-verifies | Exit 0 only on `DEMONSTRATED`; 1 on denial or failed restoration; 130 if you interrupt the prompt; 2 on bad arguments. Writes host state **only** while a human confirmed these exact bytes. `--plan` takes a strict JSON plan instead, and the two flags are mutually exclusive |
| `python3 -m core.cli list-faults` | Prints the unresolved emergency fault records | Read-only; exit 1 when any remains, which is also why the next campaign is denied |
| `python3 -m core.cli resolve-fault <fault-id>` | Re-reads the host, compares it with what the fault record owes, and closes the record only when they agree | It never changes the host. A 32-hex identifier is required and arbitrary paths are rejected |
| `python3 -m core.cli inspect /path/to/file [--tool inspect-with-objdump]` | Delegates analysis to one REA CLI command (default `inspect-artifact`) | Starts the local REA Node runtime; exits non-zero unless REA succeeds |

The Sentinel no longer accepts `--human-approved` or a caller-provided tier as authority. A read-only allowlist verdict still **does not grant permission to execute a command**. Proposal review is read-only and proposals cannot supply executable commands or alter the fixed plan. Campaign authorization requires a fresh local TTY confirmation bound to both canonical proposal and plan digests; it is one-use and process-local, and it carries a monotonic deadline whose 120-second ceiling is fixed in code — a caller may shorten the lifetime, never lengthen it, and no flag or environment variable sets either; the deadline is re-checked before dispatch and before every sample, because an agent that could choose its own TTL would be handing itself more time. This ceremony expresses operator consent but does not authenticate identity or protect against malicious processes running as the same user. The fixed campaign is not connected to Sentinel, hardware **controls**, or arbitrary experiment plans; since IHRA-04 it does read the monitored CPU power surface and seal a rollback result, but it declares no intervention, so what its evidence proves is that the surface did not move. Evidence is written atomically under `$XDG_STATE_HOME/ihra/synthetic-experiments/` (or `~/.local/state/ihra/synthetic-experiments/`) with private permissions; a failure stops the sequence and preserves completed receipts.

The campaign inspector is read-only and never infers abandonment from age or PID absence. It returns `ACTIVE_OR_UNKNOWN` for a persisted `RUNNING` campaign, `INCOMPLETE` / `NOT_DEMONSTRATED` for missing or inconsistent evidence, and `COMPLETED_VALID` only when the current proposal, plan, terminal checkpoint, five receipts, the `prestate`/`poststate`/`rollback` triple and the manifest all agree — and only when the rollback status re-derived from those two states matches the label that was recorded. Historical evidence remains readable but is not upgraded to `COMPLETED_VALID` when it lacks the current proposal/checkpoint links or the rollback artifacts.

Real Linux tests now also kill the campaign parent with `SIGKILL` while the fixed Bubblewrap workload is active and after a previous sample receipt is durable. In both tested timings the captured sandbox descendants exit, prior receipt evidence remains verifiable, and inspection does not report success. These are isolated test-harness runs with temporary evidence, not a standalone evidence bundle or proof of human authorization. In the Tier-1 path, by contrast, `SIGINT`, `SIGTERM`, `SIGHUP` and `SIGQUIT` are covered: the test delivers a real signal to the running process inside the guarded window, and the handler restores the profile and re-reads it before the campaign is reported. `SIGKILL` stays unreachable for any handler, and power loss, storage failure and automatic recovery/resumption remain untested or unavailable.

## Protocol and evidence

The [Common Hardware Research Pipeline](COMMON_RESEARCH_PIPELINE.md) describes the **target workflow**, not a fully implemented unattended service:

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
- Tier 1 is executable only with a human at this TTY, and Tier 2 does not exist as a code path. `run-tier1` will set one allowlisted `powerprofilesctl` profile after the confirmation binds the plan digest and the intervention digest, and it refuses to finish unless the restoration is re-read and verified. `evaluate_plan()` — everything an unattended caller can reach — still denies every state change, so there is no autonomous hardware authority.
- Tier 1 was exercised on the real host twice on 2026-11-06, the operator typing each confirmation in their own terminal. The first: `run-tier1 --set performance` completed as `RESTORED_VERIFIED` in 0.84 s, `powerprofilesctl get` and every other monitored field (governor, EPP, boost, driver) came back to the captured prestate, all seven artifacts verified against the bundle manifest, and the fault ledger stayed empty. One defect that round made visible — the generated plan declared `workload.repetitions = 3` because the validator requires at least three, while the runner recorded a single post-change observation and reported `samples_completed: 1` — is now closed: the Tier-1 runner observes the monitored surface once per declared repetition, reports that observed count, and refuses any plan declaring more than `MAX_TIER1_OBSERVATIONS` (16) so one authorized campaign cannot become an unbounded read loop. Observations are plain file reads, so the authorized commands stay exactly two: the change and the restoration. The second round tested the interruption path: `SIGTERM` arrived after the authorized change had landed on the host but before the intervention receipt was sealed, the signal handler restored the profile, and the campaign closed `ABORTED`/`REFUTED` with `RESTORED_VERIFIED`, an empty `differences` list and no fault record; its bundle carries five artifacts and no `measurement.json`, which is what a signal that early honestly means — and why that round does not demonstrate the repetition count on real hardware, only the tests do. See `plans/ihra-autonomy-safety-prerequisites.md`, *Ronda Tier-1 real consentida*.
- The rollback helper is no longer separate: `RollbackContract` runs inside both campaign runners, and the recorded status is derived from before/after reads. What it still does not provide is attestation (receipts are hashed, not signed), a supervisor that repairs or restores the host on next launch, or any rollback for a crash outside the guarded window.

See [Safety contracts](SAFETY_CONTRACTS.md), [12 proposed adversarial scenarios](ANTIFAKE_TEST_MATRIX.md), and the [implementation plan](IMPLEMENTATION_PLAN.md) for the stronger design intended by the project.

## Research origins

These are **source case studies**, not optimizations performed automatically by IHRA:

**A. Optimus `D3cold` / RTX 4050 Laptop.** A boot-time Ada unlock appeared successful to the driver, but a subsequent power-state transition reset volatile hardware registers while the software getter still reported full rate. The decisive control compared physical BAR0 state and actual instruction throughput. On one measured laptop, the retained unlock approached **2× in a tensor instruction probe**; cuBLAS GEMM improved by smaller workload-dependent amounts. See the [IHRA case study](HISTORY_ADA_D3COLD.md), [original evidence](https://github.com/DannyBaanks/gpu-unlock-ada/tree/main/docs/evidence/ada28a1), and [upstream PR #134](https://github.com/JustAResearcher/gpu-unlock-ada/pull/134).

**B. REA-CPU-01 / Ryzen 5 8645HS.** A counterbalanced power-profile campaign reported approximately **+12.6% sustained clock** but **+5.3% in a particular CPU microbenchmark** on one HP Victus. The XDNA device was detected, but useful user-space NPU inference was not established; no supported safe write path for the explored SMU controls was identified. See the [CPU case study](HISTORY_REA_CPU_01.md). This is **not** a universal CPU boost.

### Related repositories

- [REA — reverse-engineering tools](https://github.com/DannyBaanks/rea): companion research tooling with its own prerequisites and security considerations.
- [gpu-unlock-ada — experimental GPU work](https://github.com/DannyBaanks/gpu-unlock-ada): separate kernel/firmware research; **not installed or run automatically by IHRA**.

## Tests and reproducibility

```bash
python3 -m unittest discover -s tests -v
```

The repository contains `unittest` tests for mocked adapters, Sentinel allowlist denials, constrained rollback behavior, verifier input bounds, required hashes, fixture replay, the fixed workload executor, ten synthetic campaign adversarial controls, read-only campaign recovery inspection, simulated cuts at persistence boundaries, and two real Linux/Bubblewrap parent-`SIGKILL` timings. IHRA-04 added four suites: 14 authorization-expiry and grant-binding tests, 16 Sentinel plan-validation tests that also pin the schema against the validator, 9 tests that a campaign seals coherent rollback evidence, and 9 Tier-1 safety tests that run the whole consent, intervention, signal and fault cycle against a fake `powerprofilesctl` host — including the check that an unattended path is denied without a single subprocess. The suite is 191 tests. The [IHRA-01 control report](SYNTHETIC_EXPERIMENT_CONTROLS.md) separates simulated controls from real process checks and records the verified campaign and timeout evidence. The five-sample campaign demonstrates repeatability only—not performance or hardware optimization. Checkpoints, read-only inspection, and the two tested interruption timings are demonstrated; automatic recovery/resumption, power-loss or storage-failure behavior, autonomous planning, general authorization, and unattended hardware changes are not demonstrated or provided.

Consult [fixtures](../fixtures/), [experiment schema](../schemas/experiment.schema.json) and [evidence schema](../schemas/evidence.schema.json) for the current test contracts. Synthetic fixtures are not raw hardware execution logs.

## Safety and publication

- Read-only research is the default **policy goal**, not a claim of universal OS-level isolation.
- Tier 3 operations such as direct firmware, SMU or MSR writes are forbidden by the documented protocol; the current string-based gate is **not a substitute** for an OS-enforced deny-by-default executor.
- No automatic overclocking, undervolting, BIOS modification or thermal-limit bypass is provided or endorsed.
- **Not covered by the contracts above:** `experiments/cpu-boost/run_boost_matrix.py` writes `/sys/devices/system/cpu/cpufreq/boost` under `sudo`, and `recipes/smart-boost/install.sh` installs a udev rule and a service that keep writing the same knob. Both re-read the node around their jobs and restore it on signals, but they run **outside** the Sentinel gate, outside `PlanAuthorization`, outside `RollbackContract` and outside the campaign manifest, and their signal handler prints the read-back instead of comparing it with the captured value. They are operator-run scripts, not IHRA capabilities, and closing that gap is IHRA-05 work.
- Never publish hostnames, serials, GPU UUIDs, tokens or raw private traces without a separate redaction review.

**License: MIT.** The repository root contains the MIT grant in [`LICENSE`](../LICENSE), and `package.json` declares the same identifier. DannyBaanks selected MIT; the earlier Apache-2.0 proposal is superseded.

**Status:** publicly available **HRA-00 research prototype**. See [ROADMAP.md](../ROADMAP.md) for the original staged proposal; older notes referring to a not-yet-created public repository are historical, not the current publication state.
