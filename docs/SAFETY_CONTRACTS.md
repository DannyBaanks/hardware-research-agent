# Safety Contracts & Threat Model: IHRA

> These contracts describe the intended policy, not guarantees already enforced by the prototype. Current code classifies an exact read-only allowlist, denies state-changing Sentinel requests, replays fixed synthetic fixtures, runs one pinned CPU-only workload in a rootless Bubblewrap sandbox, and can orchestrate one fixed five-sample synthetic repeatability campaign after an explicit local TTY confirmation bound to its proposal and plan digests. Proposals are inert, closed-schema data. TTY confirmation is not cryptographic identity and does not protect against malicious processes running as the same UID. IHRA does not implement an executor for arbitrary experiment plans or an authenticated write-approval lifecycle; see `README.md`.

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

---

## 5. Threat Model & Privacy Redaction

### Threat 1: Malicious Benchmark / Prompt Injection
* **Attack:** A benchmark script or external workload attempts to execute arbitrary shell commands or exfiltrate credentials.
* **Mitigation:** Benchmarks must be deterministic binaries or pure-math scripts without network access, running strictly under unprivileged user permissions.

### Threat 2: Privacy Leakage in Evidence Bundles
* **Threat:** Evidence bundles contain private identifiers (MAC address, board serial number, machine UUID, local usernames, absolute directory structures).
* **Mitigation:** The `EvidenceRecorder` runs an automated redaction filter on all logs before computing SHA-256 sums:
  * User home paths (`/home/username/...`) are normalized to `~` or generic relative paths.
  * MAC addresses are masked to `XX:XX:XX:XX:XX:XX`.
  * Machine UUIDs in `/etc/machine-id` and DMI serials are replaced with pseudorandom hashes.

### Threat 3: Host Resource Starvation (OOM / Thrashing)
* **Threat:** Parallel compiler bursts or heavy vector benchmarks exhaust physical memory, triggering kernel thrashing and freezing the host desktop.
* **Mitigation:** The `ExecutionOwner` polls memory before dispatching tasks. If available memory is less than $1.5\text{ GB}$ and Swap is above $80\%$, execution is queued or aborted with `RESOURCE_PRESSURE_ABORT`. **Status:** not implemented; no sensor polling exists.

---

## 6. Authority Lifetime and Execution Coupling

This is the contract IHRA-04 implements. It is a specification, not a claim about current code.

### 6.1 Expiration

* Authority validity is decided on a **monotonic deadline** (`time.monotonic`). Wall-clock time never participates in an authorization decision.
* `issued_at` and `expires_at` are additionally recorded in ISO-8601 UTC inside evidence artifacts so a human can read them; those fields are reporting only.
* `DEFAULT_TTL_SECONDS = 120` is fixed in code. It must be greater than or equal to the authorized plan's `max_total_duration` (60 s today), so a valid grant cannot expire mid-campaign while the campaign is still inside its own ceiling.
* **TTL is not a caller-supplied parameter.** No function argument, CLI flag or environment variable may lengthen it: an agent that can choose its own TTL grants itself more time.

### 6.2 Grant token

* Every grant carries a token: `HMAC-SHA256(key, plan_sha256 || proposal_sha256 || issued_at)`.
* The key is generated per process with `secrets.token_bytes(32)` and is never persisted.
* **What the token is:** an anti-replay and self-consistency binding inside one process, so a grant cannot be reused, reordered or rebuilt from a different plan.
* **What the token is not:** identity. A process running as the same UID can read the key from process memory. Consistent with §1.3 and with the TTY warning in `core/authorization.py`, no cryptographic human identity is claimed here.

### 6.3 Digest coupling

* The command that executes must be byte-identical to the command that Sentinel classified, proven through the canonical plan digest rather than through a matching string.
* A grant is re-validated before dispatch and before every sample. Expiration is checked **before** a single-use grant is marked consumed, so an expired grant does not burn the TTY ceremony.

### 6.4 Rollback coupling

* `rollback_status` is derived from observed prestate and poststate reads. It is never a literal.
* A failed prestate read denies the experiment; recording it as `None`, empty or `unknown` is forbidden, because a missing baseline silently converts "we do not know the state" into "the state was fine".
* Restoration is verified by re-reading state; an unverified restoration is a `FAULT_STATE`, which blocks subsequent experiments until resolved.

### 6.5 Known gap: real hardware writes outside this contract

`experiments/cpu-boost/run_boost_matrix.py` writes `/sys/devices/system/cpu/cpufreq/boost` under `sudo`. It re-reads the knob around every job and restores it on signals, but it does so **outside** the Sentinel gate, outside `PlanAuthorization`, outside `RollbackController` and outside the campaign manifest, and its signal handler prints the read-back instead of comparing it with the captured value. `recipes/smart-boost/install.sh` installs a udev rule and a service that keep writing the same knob. Closing this is IHRA-05 work; until then this document does not describe that script.
