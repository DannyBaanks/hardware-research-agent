# Safety Contracts & Threat Model: IHRA

> These contracts describe the policy the code enforces today, with the limits spelled out. Current code classifies an exact read-only allowlist and denies state-changing requests in unattended mode; runs a fixed five-sample synthetic campaign only after a one-use local TTY confirmation bound to its proposal and plan digests, with a monotonic expiry; performs one reversible `powerprofilesctl` profile change only when that same TTY confirmation is bound to the exact plan bytes **and** to the intervention the plan declares, capturing the host state first, persisting prestate/poststate/rollback evidence into the campaign manifest, and always running and re-verifying the authorized restoration; refuses every later campaign while a restoration it could not verify is on record. Proposals are inert, closed-schema data. TTY confirmation is not cryptographic identity and does not protect against malicious processes running as the same UID. IHRA does not implement an executor for arbitrary experiment plans, Tier 2 execution, sensor polling, or automatic repair of the host after a crash; see `README.md`.

The observed adversarial results and the distinction between simulated tests and real Linux process checks are recorded in [IHRA-01 Synthetic Experiment Controls](SYNTHETIC_EXPERIMENT_CONTROLS.md). A passing five-sample campaign demonstrates repeatability and contract analysis only; it is not evidence of performance improvement or hardware autonomy.

## 1. Core Safety Tenets

1. **Safety Over Performance:** A system must never be made faster at the expense of making it less safe, less predictable, or less understandable.
2. **Denial is a Valid Victory:** When an optimization cannot be demonstrated safely or reversibly, issuing `DENY` or concluding `NO_SAFE_GAIN_FOUND` is a successful research outcome.
3. **Zero Implicit Privilege Escalation:** The agent never invokes `sudo` or acquires root access implicitly. Any privileged operation requires explicit human consent and a pre-compiled whitelist command.
4. **Automated Rollback Mandate:** No modification may be initiated unless a verified, automated rollback path exists and has been tested.
5. **Fail-Closed Design:** Any anomaly, unexpected error, sensor failure, or unhandled exception immediately halts testing and initiates rollback.

---

## 2. Risk Tier Classification

| Risk Tier | Name | Definition | Authorization Requirement | Permitted in V1? |
| :---: | :--- | :--- | :--- | :---: |
| **Tier 0** | **Read-Only Inspection** | Queries to sysfs, `/proc`, `lscpu`, `lspci`, sensor reads, reading public ACPI tables, standard user-space benchmarks. | Automated (Implicit) | **YES** |
| **Tier 1** | **User-Space Reversible** | Standard DBus/PolicyKit switches (e.g. `powerprofilesctl set`, setting CPU governor within kernel limits). | Explicit Human Consent | **YES** |
| **Tier 2** | **Privileged Reversible** | Modprobe options (`/etc/modprobe.d/`), modifying tempfiles rules, service restarts via systemd. | Explicit Human Consent + Rollback Hook | **REQUIRES SEPARATE AUTHORIZATION** |
| **Tier 3** | **Dangerous / Destructive** | Writing to BIOS/UEFI/firmware ROMs, direct SMU mailbox writes, direct EC fan register writes, MSR writes, undervolting/overclocking. | **STRICTLY FORBIDDEN** | **NO** |

**What this table means in code today.** Tier 0 is the Sentinel allowlist: exact, no-argument read-only commands, classified without executing anything. Tier 1 is reachable only through `python3 -m core.cli run-tier1`, only for the single `powerprofilesctl set <supported profile>` command, and only with a one-use local TTY confirmation bound to the exact plan bytes and to the intervention those bytes declare; `SentinelGate.evaluate_plan()` still denies Tier 1 for every unattended caller, so autonomy does not acquire a write path. Tier 2 has no code path at all — nothing in `core/` writes modprobe options, tmpfiles rules or systemd units. Tier 3 is denied twice over, by the forbidden patterns and by the allowlist.

---

## 3. Explicit List of Forbidden Operations

The following operations are strictly forbidden from automated agent execution:

1. **Firmware & BIOS Flashing:** No writing to SPI flash, UEFI NVRAM variables, Falcon firmware, or microcode updates.
2. **SMU & SMN Mailbox Writes:** No writing to PCI `00:00.0` offsets `0x60/0x64` or sending custom message IDs to `0x03B10A20`.
3. **MSR (Model-Specific Register) Overrides:** No writes to `/dev/cpu/*/msr` or modifying voltage FID/VID registers.
4. **Thermal Protection Disabling:** No modifying critical thermal trip points in ACPI, no disabling PROCHOT, no disabling hardware shutdown thresholds.
5. **Embedded Controller (EC) Poking:** No direct read/write to EC I/O ports (`0x62/0x66` or ACPI EC spaces) without verified hardware schematics and explicit human sign-off.
6. **Destructive Filesystem Operations:** No recursive directory deletions, no overwriting system drivers, no editing kernel binaries.
7. **Uncontrolled Power Cycling:** No automated hard reboots or power cutting as a mechanism to reset state.

---

## 4. Rollback Verification Contract

Every Tier 1 or Tier 2 experiment must implement the `RollbackContract` interface:

```python
class RollbackContract:
    def capture_prestate(self) -> dict:
        """Captures the exact state before intervention (e.g. current profile)."""
        pass
        
    def execute_rollback(self, prestate: dict) -> bool:
        """Executes the restorative command."""
        pass
        
    def verify_restoration(self, prestate: dict) -> bool:
        """Re-reads the hardware/sysfs node to confirm state matches prestate."""
        pass
```

If `verify_restoration()` returns `False`, the agent enters an **Emergency Fault State**, alerts the operator, prints immediate manual remediation commands, and refuses to run further experiments.

**Implementation status.** `core/rollback.py` implements this interface over the monitored CPU power surface (`scaling_driver`, `governor`, `energy_performance_preference`, `boost_enabled`, `boost_control`, `platform_profile` — read from sysfs only, with no subprocess and no sleeping per observation), and `core/sentinel.py` refuses any state-changing plan whose declared `restoration_command`, `verification_query` or `expected_restored_state` this controller could not verify. Two runners are coupled to it:

* `core/experiment.py` (fixed synthetic campaign, Tier 0): captures the prestate before dispatch, denies the campaign if that read fails, and seals `prestate.json`, `poststate.json` and `rollback.json` into the campaign directory under its SHA-256 manifest.
* `core/tier1.py` (one authorized profile change): refuses a plan declaring more than `MAX_TIER1_OBSERVATIONS` (16) repetitions before it touches the host, refuses to start unless the host is already where the authorized restoration puts it, observes the monitored surface once per declared repetition and records that observed count in `measurement.json` alongside `intervention.json`, and runs plus re-verifies the restoration on every exit path, including from a `SIGINT`/`SIGTERM`/`SIGHUP`/`SIGQUIT` handler.

`rollback_status` is derived from those two observations and is never supplied by a caller: `VERIFIED_UNCHANGED` (nothing was changed and the surface still matches), `RESTORED_VERIFIED` (it was changed and the restoration matched the prestate), `FAILED_STATE_DIFF` (it differs and stands). `FAILED_STATE_DIFF` writes one emergency fault record under `$XDG_STATE_HOME/ihra/tier1-faults/` with the remediation commands, and from then on every Tier-1 and synthetic campaign is denied until a person runs those commands and `python3 -m core.cli resolve-fault <id>`, which only re-observes the host and never writes it. A campaign that drifted without declaring an intervention is recorded, not treated as an emergency: IHRA does not know who moved the host, so it does not claim a repair obligation it never took. A record that cannot be read — wrong permissions, wrong owner, a directory, or bytes that are not a single JSON object — is denied through the same `FaultStateError` vocabulary as an unreadable directory, so `list-faults`, `resolve-fault` and both campaign gates report the refusal as JSON or `status DENIED` and never as a traceback in front of an operator who has just typed their authorization.

**Write-ahead intent (2026-11-12).** Before `core/tier1.py` changes the profile it writes a pending-intent record into the same ledger: an `UNRESOLVED` record with `rollback_status: PENDING_CLOSE_OUT`, carrying the captured prestate, the restoration command and the remediation lines. It is removed only after the restoration is verified, and replaced under the same id by the sealed fault when it is not. If the record cannot be written the campaign aborts with the host untouched. A campaign that dies where no handler runs — `SIGKILL`, power loss — therefore leaves the intent behind, `list-faults` reports it, and every later campaign is denied until a person restores the host and runs `resolve-fault <id>`. A test kills a real child process with `SIGKILL` after the profile moved and checks exactly that. This is detection, not repair. The window between the intent reaching disk and the profile moving is covered in the safe direction: a kill there leaves an intent over an unchanged host, which `resolve-fault` closes without anyone changing anything.

What is **not** implemented: no supervisor restores the host when IHRA is next launched after a power loss or a `SIGKILL` (see `docs/ANTIFAKE_TEST_MATRIX.md` AF-09), fault records are not cryptographically signed, and Tier 2 restoration hooks do not exist.

---

## 5. Threat Model & Privacy Redaction

### Threat 1: Malicious Benchmark / Prompt Injection
* **Attack:** A benchmark script or external workload attempts to execute arbitrary shell commands or exfiltrate credentials.
* **Mitigation:** Benchmarks must be deterministic binaries or pure-math scripts without network access, running strictly under unprivileged user permissions.

### Threat 2: Privacy Leakage in Evidence Bundles
* **Threat:** Evidence bundles contain private identifiers (MAC address, board serial number, machine UUID, local usernames, absolute directory structures).
* **Mitigation as designed (NOT IMPLEMENTED):** the `EvidenceRecorder` described here, which runs an automated redaction filter over every log before computing SHA-256 sums (home paths normalized to `~`, MACs masked, machine and DMI identifiers replaced by pseudorandom hashes), does not exist. No redaction step runs anywhere in `core/`, and there is no `core/recorder.py`. `AGENTS.md` requires the same check to be done by hand before anything is staged or published.
* **What actually limits exposure today:** nothing that IHRA records needs a private identifier. The synthetic and Tier-1 bundles store canonical plan and proposal bytes, digests, allowlisted profile names, the monitored sysfs node values, counters, timestamps and typed receipts; the host reads are the fixed allowlisted read-only commands. Two exceptions are explicit: an emergency fault record stores the absolute `evidence_dir` of the campaign that failed, which contains the local username, and a failure `reason` can echo the text of an OS error. Publishing a bundle remains a human step, so check before attaching it to an issue:

  ```bash
  grep -RInE '([0-9a-f]{2}:){5}[0-9a-f]{2}|/home/|machine-id' <bundle-or-record>
  ```

### Threat 3: Host Resource Starvation (OOM / Thrashing)
* **Threat:** Parallel compiler bursts or heavy vector benchmarks exhaust physical memory, triggering kernel thrashing and freezing the host desktop.
* **Mitigation:** The `ExecutionOwner` polls memory before dispatching tasks. If available memory is less than $1.5\text{ GB}$ and Swap is above $80\%$, execution is queued or aborted with `RESOURCE_PRESSURE_ABORT`. **Status:** not implemented; no sensor polling exists.

---

## 6. Authority Lifetime and Execution Coupling

This is the contract IHRA-04 was written for and implemented in M1–M4. Each item below names the code that enforces it and the test that would fail if it drifted.

### 6.1 Expiration

* Authority validity is decided on a **monotonic deadline** (`time.monotonic`). Wall-clock time never participates in an authorization decision.
* `issued_at` and `expires_at` are additionally recorded in ISO-8601 UTC inside evidence artifacts so a human can read them; those fields are reporting only.
* `DEFAULT_TTL_SECONDS = 120` is fixed in code. It must be greater than or equal to the authorized plan's `max_total_duration` (60 s today), so a valid grant cannot expire mid-campaign while the campaign is still inside its own ceiling.
* **TTL is not a caller-supplied parameter.** No function argument, CLI flag or environment variable may lengthen it: an agent that can choose its own TTL grants itself more time. A caller may only shorten it, and `_validate_ttl` rejects a non-numeric, zero, negative or oversized value.
* Enforced in `core/authorization.py` (`remaining_seconds`, `expired`, the backwards-clock rule) and `SentinelGate.verify_grant`; held by `tests/test_grant_expiry.py`, including `test_expired_grant_denies_before_dispatch`, `test_expired_grant_is_not_consumed` and `test_ttl_revalidated_before_each_sample`.

### 6.2 Grant token

* Every grant carries a token: `HMAC-SHA256(key, plan_sha256 || experiment_id || expires_at)`, joined with NUL separators over the canonical plan digest and the monotonic deadline.
* The key is generated per process with `secrets.token_bytes(32)` and is never persisted.
* **What the token is:** an anti-replay and self-consistency binding inside one process, so a grant cannot be reused, reordered or rebuilt from a different plan.
* **What the token is not:** identity. A process running as the same UID can read the key from process memory. Consistent with §1.3 and with the TTY warning in `core/authorization.py`, no cryptographic human identity is claimed here.
* Held by `test_grant_binds_the_plan_digest`, `test_grant_rejects_a_mutated_plan` and `test_forged_grant_object_is_rejected`.

### 6.3 Digest coupling

* The command that executes must be byte-identical to the command that Sentinel classified, proven through the canonical plan digest rather than through a matching string.
* A grant is re-validated before dispatch and before every sample. Expiration is checked **before** a single-use grant is marked consumed, so an expired grant does not burn the TTY ceremony.
* A Tier-1 state change binds a third digest: `intervention_sha256`, taken over the canonical bytes of the intervention the gate derives from the plan's own declared command and rollback (`tier1_intervention_digest`). A grant minted for one intervention cannot be spent on another plan, and a grant minted without one cannot open the Tier-1 leg at all. Tier 1 has no proposal artifact, so the ceremony records the plan digest in the proposal slot as well and says so in the emitted evidence.
* The answer must follow the display. Before `_read_confirmation` writes the confirmation prompt it discards whatever is already queued on the terminal (`termios.tcflush` with `TCIFLUSH` — input only, because flushing output would drop the proposal bytes that are still on their way to the screen). A line already sitting in the terminal queue when the prompt is reached — written there by a launcher, by `printf … > /dev/tty`, or left by an earlier run — can no longer satisfy the ceremony, and if the queue cannot be cleared the confirmation fails closed. What the drain guarantees is exactly that: whatever is queued at that instant is discarded. Whether an interactive shell forwards a human paste to the terminal queue or keeps it in its own line editor is shell behaviour outside this code, and the one live paste this session examined was consumed by the shell, never by the confirmation read. This protects the quality of consent, not a privilege boundary: a same-UID process remains outside the model (§1.3). Held by `tests/test_tty_authorization.py`.
* `SentinelGate.verify_grant` re-checks Tier-0 grants; a Tier-1 grant does not pass it and falls closed. Tier-1 authority is spent instead by `PlanAuthorization.consume` against all three digests. Held by `tests/test_tier1_safety.py`.

### 6.4 Rollback coupling

* `rollback_status` is derived from observed prestate and poststate reads. It is never a literal: `VERIFIED_UNCHANGED`, `RESTORED_VERIFIED` or `FAILED_STATE_DIFF` (`ROLLBACK_*` in `core/rollback.py`), and `core/recovery.py` re-derives it from the sealed states when it inspects a campaign, so relabelling a drift as "unchanged" is rejected.
* A failed prestate read denies the experiment; recording it as `None`, empty or `unknown` is forbidden, because a missing baseline silently converts "we do not know the state" into "the state was fine". `monitored_state` raises instead of returning `None`.
* An unverified restoration is `FAILED_STATE_DIFF`, and it writes an emergency fault record that blocks subsequent Tier-1 and synthetic experiments until a person resolves it (§4). Automatic repair on the next launch is **not** implemented: IHRA stops, it does not fix.
* Held by `tests/test_campaign_rollback.py` and `tests/test_tier1_safety.py`; the recovery side by `tests/test_campaign_recovery.py`.

### 6.5 Known gap: real hardware writes outside this contract

`experiments/cpu-boost/run_boost_matrix.py` writes `/sys/devices/system/cpu/cpufreq/boost` under `sudo`. It re-reads the knob around every job and restores it on signals, but it does so **outside** the Sentinel gate, outside `PlanAuthorization`, outside `RollbackController` and outside the campaign manifest, and its signal handler prints the read-back instead of comparing it with the captured value. `recipes/smart-boost/install.sh` installs a udev rule and a service that keep writing the same knob. Closing this is IHRA-05 work. Until it is closed, both are **operator-run tools, not IHRA capabilities**: `README.md`, [TECHNICAL](TECHNICAL.md) and [TECHNICAL (es)](TECHNICAL_es.md) each say so where they are listed, and no contract in this document covers them or guarantees their restoration.
