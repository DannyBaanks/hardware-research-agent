# Hardware Research Agent (IHRA) — Agent Contract

## 0. Prime Directives
1. **The Product is the Scientific Method, Not the Unlock:** Never fabricate an optimization or exaggerate a benchmark. An experiment concluding `NO_SAFE_GAIN_FOUND` is a successful research milestone.
2. **Read-Only by Default:** Zero writes to system hardware or persistent configurations without explicit human authorization.
3. **Automated Rollback Mandate:** No modification may be initiated without a verified, automated rollback hook and post-restoration verification query.
4. **Anti-Fake Triangulation:** Software getters are not physical proof. Always triangulate:
   $$\text{Software Telemetry} \land \text{Hardware Readback} \land \text{Physical Execution Throughput} \land \text{Mathematical Parity}$$
5. **No Tier 3 Actions:** BIOS/firmware flashing, SMU register writes, direct MSR writes, and disabling thermal trip points are strictly forbidden under all circumstances.

## 1. Structure & Workflows
* `docs/`: Master forensic case studies (`HISTORY_ADA_D3COLD.md`, `HISTORY_REA_CPU_01.md`), protocol specs (`COMMON_RESEARCH_PIPELINE.md`, `ARCHITECTURE.md`, `SAFETY_CONTRACTS.md`), and test matrices.
* `core/`: Decoupled engine modules — `discovery.py`, `sentinel.py`, `verifier.py`, `rollback.py`, `authorization.py`, `experiment.py`, `tier1.py`, `recovery.py`, `executor.py`, `workload_runner.py`, `planning.py`, `cpu_power.py`, `evidence.py`, `cli.py`. There is no `hypothesis.py` and no `recorder.py`.
* `adapters/`: Hardware capability detectors — the four Linux probes (`LinuxInventoryAdapter`, `LinuxCpuPerfAdapter`, `NvidiaTelemetryAdapter`, `AmdXdnaDiscoveryAdapter`) live as classes in `adapters/adapters.py`; `ada_adapter.py` and `rea_adapter.py` carry the case-study probes; `base.py` defines `HardwareAdapter` and `CapabilityStatus`.
* `fixtures/`: Synthetic failure cases (including the Ada D3cold trap).
* `tests/`: Automated unit and adversarial test suite.
* **Acting as an agent:** the CLI is the only sanctioned surface. `sentinel` classifies without executing, and Tier-0 read-only commands are yours to run. `verify-evidence` is one of them: use it to judge a measurement file instead of computing a gain yourself, and quote its verdict with its reason. Anything that changes state (`run-tier1`) must be launched by the human in their own terminal, because the authorization is theirs, not yours — you prepare it, explain the exact undo, and hand over the command line. Before proposing another campaign, run `list-faults`: an open record means the host is not where a plan said it was, and IHRA will deny execution until that record is resolved.

## 2. Before Committing
* Run `python3 -m unittest discover -s tests -v` (must exit 0 with 0 failures). The suite is hermetic: tests build their host from `tests/fake_host.py` and must never read this machine's `/sys`, state directory or `powerprofilesctl`. A test that only passes on one machine is a defect in the test.
* `.github/workflows/ci.yml` runs the same suite on every push, once on a bare runner and once with the real Bubblewrap sandbox where sandbox tests may not be skipped. A skip is acceptable only for a capability the host lacks; never skip or weaken a test to get green.
* Verify zero private identifiers (MAC, board serials, tokens) in staged files.
