# The Common Hardware Research Pipeline (CHRP)

## 1. Philosophical Principle
A hardware research agent must never be an unconstrained "optimizer" that guesses tweaks until a benchmark spikes. 

**The core product is the scientific method applied to hardware, not the unlock.**

The pipeline is designed to:
* Discover physical capabilities before reading marketing names.
* Measure baselines before forming opinions.
* Treat software getters as untrusted claims until verified by physical execution.
* Maintain negative results as legitimate scientific victories (`NO_SAFE_GAIN_FOUND`).
* Enforce strict authorization and automated rollback before any state change.

```
+----------+     +----------+     +-------------+     +---------+     +-------------+
| DISCOVER | --> | BASELINE | --> | HYPOTHESIZE | --> | CONTROL | --> | RISK REVIEW |
+----------+     +----------+     +-------------+     +---------+     +-------------+
                                                                             |
+----------+     +----------+     +-------------+     +---------+            v
|  RETURN  | <-- | EVIDENCE | <-- |   RESTORE   | <-- | VERIFY  | <--  +-----------+
+----------+     +----------+     +-------------+     +---------+      | AUTHORIZE |
                                                                       +-----------+
                                                                             |
                                                                             v
                                                                       +-----------+
                                                                       | EXPERIMENT|
                                                                       +-----------+
```

---

## 2. The 10-Stage Protocol

### Implementation status (2026-11-06)

The ten stages below are the **protocol as designed**. This table says what `core/` actually runs
today, so no stage reads as satisfied just because it is written down. Detailed limits:
[TECHNICAL](TECHNICAL.md), contracts: [SAFETY_CONTRACTS](SAFETY_CONTRACTS.md).

| Stage | Status in code | What exists, and what does not |
|---|---|---|
| 1 DISCOVER | Implemented | `core/discovery.py` and `adapters/`, read-only (`O_RDONLY`). Some detection relies on fixed ID and path lists. |
| 2 BASELINE | Partial | A campaign captures the monitored host state before it acts (`RollbackContract.capture_prestate`) and keeps its own sample receipts, but there is no performance-baseline service and no clock/watt reference model. |
| 3 HYPOTHESIZE | Not implemented | No `core/hypothesis.py`. Sentinel only *validates* that a plan declares `hypothesis_id` and `primary_variable`; formulating the hypothesis stays a human-agent conversation. |
| 4 CONTROL | Partial | A state-changing plan is denied unless it carries a `controls` object, and `AntiFakeVerifier` refuses a claim without an explicit negative-control delta. The one campaign IHRA can run itself has a single fixed plan with no control pair, so its result is repeatability, not causation. |
| 5 RISK REVIEW | Implemented | `SentinelGate.evaluate_plan()`: tier derived from the command, Tier-3 patterns denied, unverifiable or incomplete rollback denied. Classifies; it is not an OS-enforced executor. |
| 6 AUTHORIZE | Implemented, narrowly | One-use local TTY confirmation bound to canonical digests, with a monotonic expiry that cannot be lengthened by the caller. The "cryptographic session nonce" is a per-process HMAC token — anti-replay, never identity. Tier 1 executes only with a person present (`core/tier1.py`); Tier 2 has no code path. |
| 7 EXPERIMENT | Two runners only | The fixed five-sample sandbox campaign and one `powerprofilesctl` profile change. There is no executor for arbitrary experiment plans. |
| 8 VERIFY | Implemented with limits | Median ratio across ≥3 runs per side, required hashes, negative-control check, D3cold false-positive rule. No formal significance test, no per-GHz normalization, no physical readback of its own. |
| 9 RESTORE | Implemented, bounded | Restoration runs and is re-read whether the campaign succeeded, failed or was interrupted inside the guarded window, and the status is derived from the observed states: an unverified restoration demotes that campaign to `ABORTED`, and in the Tier-1 path it also seals the emergency fault that blocks every later run. It cannot run for `SIGKILL` or a crash outside the window, and nothing restores the host on the next launch; since 2026-11-12 a Tier-1 campaign killed that way leaves the pending-intent record it wrote before the change, which blocks every later run until a person restores the host and resolves it. |
| 10 EVIDENCE & RETURN | Partial | Private, atomic, manifest-hashed bundles outside the repository, inspectable read-only. The privacy sanitizer described at this stage does not exist, and `docs/ANTIFAKE_TEST_MATRIX.md` records AF-07 as demonstrated for campaigns only. |

### Stage 1: DISCOVER (Hardware & Environment Inventory)
* **Objective:** Establish the ground truth of the machine's physical silicon, bus topology, firmware revisions, and kernel drivers.
* **Input:** System sysfs nodes (`/sys/devices/system/cpu/`, `/sys/bus/pci/devices/`, `/sys/class/hwmon/`, `/sys/firmware/acpi/`).
* **Required Data:**
  * CPU architecture, family, model, core/thread count, vector ISA flags.
  * PCI device IDs, vendor IDs, subsystem IDs, and active kernel modules.
  * Memory topology: physical RAM, active swap devices, ZRAM instances.
  * ACPI platform profiles and active driver (`amd-pmf`, `intel-rapl`, `hp-wmi`).
* **Forbidden Actions:** No register writes, no probing of undocumented ports, no modifying permissions.
* **Output:** `HardwareInventory` struct.

### Stage 2: BASELINE (Non-Invasive Reference Capture)
* **Objective:** Record system behavior, background tasks, thermal equilibrium, and reference performance *before* any intervention.
* **Checks:**
  * Detect concurrent workloads (compilations, other agents, background daemons).
  * Ensure system is in thermal steady-state (not cooling down from a previous thermal spike).
  * Capture baseline power profile, frequency distribution, and fan tachometers.
* **Benchmark:** Run a lightweight, deterministic user-space reference workload with output hashing.
* **Forbidden Actions:** No elevating process priority (`nice -20`), no affinity pinning without permission.
* **Output:** `BaselineReport` with cryptographic checksums.

### Stage 3: HYPOTHESIZE (Falsifiable Hypothesis Formulation)
* **Objective:** State an explicit, falsifiable prediction regarding a performance constraint or hardware state.
* **Structure Requirements:**
  1. Primary variable (e.g., all-core sustained clock frequency, quantization latency).
  2. Suspected bottleneck mechanism (e.g., ACPI STAPM power budget exhaustion).
  3. Predicted quantitative delta (e.g., $\ge 5\%$ speedup).
  4. Falsification condition (what result would disprove the hypothesis).
* **Forbidden Actions:** "Let's try this command and see what happens" is strictly disallowed.
* **Output:** `HypothesisRecord` added to the active ledger.

### Stage 4: CONTROL (Design of Negative & Invariant Controls)
* **Objective:** Define the control mechanisms that isolate the hypothesis from confounding factors.
* **Requirements:**
  * **Negative Control:** A test case where the hypothesized speedup *must not occur* (e.g., invariant INT4 operations when testing tensor FP16, or scalar math when testing vector units).
  * **Thermal / Run-Order Control:** Counterbalanced alternating sequence (A-B-C, C-B-A, B-A-C) to cancel thermal drift.
  * **Mathematical Parity Control:** SHA-256 hash verification of computational outputs across all runs to guarantee zero numerical corruption.
* **Output:** `ExperimentPlan` with explicitly declared controls.

### Stage 5: RISK REVIEW (Pre-Execution Safety Analysis)
* **Objective:** Categorize the proposed intervention by risk tier and verify rollback feasibility.
* **Risk Tiers:**
  * **Tier 0 (Read-Only):** Telemetry queries, sysfs reads, user-space benchmarks. Automatically approved.
  * **Tier 1 (User-Space Reversible):** Changing `powerprofilesctl` or CPU governor. Permitted with rollback hook.
  * **Tier 2 (Privileged Reversible):** Module parameters (modprobe), systemd service restarts. Requires explicit human consent.
  * **Tier 3 (Dangerous / Destructive):** Flashing BIOS, writing SMU registers, overriding EC fan limits, undervolting without protection. **STRICTLY FORBIDDEN**.
* **Gate:** If a proposed experiment lacks an automated, verifiable rollback mechanism, the review issues **`DENY`**.
* **Output:** `SafetyReviewVerdict` (`APPROVED` or `DENIED`).

### Stage 6: AUTHORIZE (Human / Sentinel Gate)
* **Objective:** Enforce separation of duties. The component that *proposes* the experiment cannot *authorize* it.
* **Mechanism:**
  * If the plan contains Tier 1+ operations, the agent displays the exact blast radius, commands, and rollback verification to the human user.
  * The human user issues explicit permission (e.g., interactive prompt or signed policy file).
* **Output:** `AuthorizationGrant` containing a cryptographic session nonce and expiration timeout.

### Stage 7: EXPERIMENT (Controlled Execution)
* **Objective:** Execute the authorized plan through a dedicated `ExecutionOwner`.
* **Rules:**
  * Execute only the pre-compiled, whitelist-approved actions.
  * Monitor thermal, memory, and kernel health continuously during execution.
  * **Emergency Abort Criteria:**
    * Temperature exceeds Tjmax threshold ($T > 99\text{ }^\circ\text{C}$).
    * Memory exhaustion ($Swap > 90\%$ or RAM $< 500\text{ MB}$).
    * Kernel logs emit Xid, MCE, or I/O failure errors.
* **Output:** `RawExecutionBundle` (unfiltered outputs, stdout/stderr, timestamps, telemetries).

### Stage 8: VERIFY (Triangulation & Anti-Fake Evaluation)
* **Objective:** Determine whether the observed result represents a genuine physical improvement or a confounding artifact.
* **The Triangulation Rule:**
  $$\text{Software Telemetry} \land \text{Hardware Readback} \land \text{Physical Execution Throughput} \land \text{Mathematical Parity}$$
* **Checks:**
  * Did invariant controls remain invariant? If controls sped up, clocks changed or test is invalid.
  * Did output hashes match baseline? If hashes differ, calculation is mathematically corrupt.
  * Did execution time decrease across multiple repetitions, or only in an isolated "best of N" burst?
* **Verdict Assignment:**
  * `DEMONSTRATED`: Hypothesis confirmed by all triangulation layers.
  * `REFUTED`: Evidence contradicts hypothesis.
  * `NOT_DEMONSTRATED`: Measurement unconvincing, confounded, or unreplicated.
  * `NO_SAFE_GAIN_FOUND`: Experiment cleanly completed with no safe performance uplift.

### Stage 9: RESTORE (Automated State Rollback)
* **Objective:** Restore the machine to its exact pre-experiment baseline state.
* **Rules:**
  * Rollback executes *regardless* of whether the experiment succeeded, failed, or aborted.
  * Verify restoration by re-reading sysfs nodes and comparing them against the Baseline capture.
  * If restoration verification fails, alert the user immediately with emergency recovery steps.
* **Output:** `RollbackVerificationReceipt`.

### Stage 10: EVIDENCE & RETURN (Sealing the Evidence Bundle)
* **Objective:** Package all raw logs, manifests, and findings into an immutable, hashed bundle.
* **Deliverables:**
  * Record all raw JSON logs, environment metadata, and timestamps.
  * Generate `SHA256SUMS.txt` covering every artifact.
  * Redact personal identifiers (UUID, MAC address, serial numbers, absolute home directory paths).
  * Return structured summary report to user.

---

## 3. Decoupling: The Principle of Non-Collapsing Roles

To prevent hallucinated victories or reckless execution, the pipeline decouples three essential roles:

1. **The Proposer (Agent / Planner):** Reads system state, identifies bottlenecks, and drafts the experiment plan. Has zero execution authority.
2. **The Sentinel (Safety Gate / Human User):** Reviews the experiment plan against the safety invariants. Has sole authority to issue `AUTHORIZE` or `DENY`.
3. **The Executor (Execution Engine):** A hardened, single-purpose runner that executes only pre-approved whitelist actions, monitors emergency abort triggers, and guarantees rollback.
