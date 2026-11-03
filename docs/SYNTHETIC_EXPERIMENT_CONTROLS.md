# IHRA-01 Synthetic Experiment Controls

This report distinguishes unit-test simulations from checks that exercised a
real process on Linux. A simulated control is evidence about the tested code
path, not proof against every live adversary.

## Adversarial controls

| Control | Condition | Expected | Observed | Evidence and execution mode | Verdict |
|---|---|---|---|---|---|
| NC-1 | A copy of the runner has different bytes from the pinned SHA-256. | Deny before the first sample. | Campaign denied; executor call count was zero. | `test_nc1_runner_tampering_is_denied_before_execution` — simulated runner path and fake executor. | `DENY` |
| NC-2 | A child exits zero but reports a result digest different from the oracle. | Reject the receipt, preserve it, and stop. | Parent validation failed the first receipt; no second sample started. | `test_nc2_exit_zero_with_wrong_digest_aborts_and_preserves_receipt` — actual executor validation with mocked `Popen` and child output. | `REFUTED` |
| NC-3 | The sandbox exceeds its wall-clock budget. | Terminate the process group, preserve failure evidence, and do not retry. | A live Bubblewrap timeout ended with exit code `-9`; the receipt failed validation, campaign aborted with zero completed samples, and sample 2 did not start. No sandbox process remained. | `test_nc3_timeout_aborts_without_retry` is simulated. Live control campaign `ec0cdc51a1454216b58e91775f71b58a`; receipt and manifest under `~/.local/state/ihra/synthetic-experiments/ec0cdc51a1454216b58e91775f71b58a/`. The test-only executor deadline was set to zero to force the timeout path. | `REFUTED` |
| NC-4 | Bubblewrap preflight reports that the sandbox is unavailable. | Deny without an unsandboxed fallback. | Campaign status was `DENIED`; zero samples ran. | `test_nc4_sandbox_absence_is_denied_and_recorded_without_fallback` — simulated `SandboxUnavailableError`. | `DENY` |
| NC-5 | The authorized in-memory plan changes after sample 1. | Stop before sample 2. | Plan mutation was detected; one executor call was made. | `test_nc5_plan_mutation_stops_before_next_sample` — simulated mutation with a fake executor. | `REFUTED` |
| NC-6 | Child JSON omits the required `iterations` field but exits zero. | Reject malformed output in the parent and stop. | Executor returned `FAILED` with exit code zero; the parent preserved a receipt with validation `FAIL`. | `test_nc6_incomplete_receipt_is_rejected` — actual executor parsing and campaign validation with mocked `Popen` and child output. | `REFUTED` |
| NC-7 | Sample 3 reports a corrupt result digest. | Preserve samples 1-3 and never start samples 4-5. | Two samples completed; sample 3 failed; exactly three executor calls occurred. | `test_nc7_corrupt_sample_stops_remaining_campaign` — simulated receipts and fake executor. | `REFUTED` |
| NC-8 | The evidence destination contains a symlink to an outside directory. | Reject before executing a workload. | Campaign returned `ABORTED` / `NOT_DEMONSTRATED`; zero executor calls occurred and the symlink target remained empty. | `test_nc8_symlink_evidence_root_is_rejected` — simulated filesystem layout in a temporary directory. No campaign receipt can be written to the rejected destination. | `NOT_DEMONSTRATED` |
| NC-9 | `PATH` contains an impostor named `bwrap`. | Resolve only the fixed trusted absolute binary paths. | The resolver returned absolute system paths, not the impostor. | `test_nc9_path_impostor_does_not_change_trusted_binary_resolution` — direct resolver check on Linux; the impostor was not executed. | `DEMONSTRATED` |
| NC-10 | The campaign receives an interruption during a sample. | Record a non-complete state and do not continue. | An interrupted marker was written; campaign status was not `COMPLETED`; one executor call occurred. | `test_nc10_keyboard_interrupt_is_recorded_as_noncomplete` — simulated `KeyboardInterrupt` with fake executor. The actual executor's process-group interruption handler is separately unit-tested with mocked process controls; abrupt parent termination was not exercised. | `REFUTED` |

`REFUTED` in this table means the fixed repeatability claim is correctly
rejected for the adversarial input; it does not mean the safety control failed.

## Real five-sample campaign

One Linux end-to-end campaign completed under the fixed plan:

- Campaign: `51c30b4d473d4621a32a08e8b7c403d2`
- State: `COMPLETED` / `DEMONSTRATED`, 5 of 5 samples
- Plan SHA-256: `135907eb2bf31e57a60ffe0a414a6fcb7b0a8cd55c744ae9984e683e61efa010`
- Runner SHA-256: `6bcbbad859e2bf01898d55196d893335fa6dee94dcc64ebcae0a379a0dde8edf`
- Result SHA-256 for all five samples: `40c778e38799bb8b101d4bb6af933608069274c553be25f62398fa852030ad68`
- Environment: Victus by HP Gaming Laptop 15-fb3xxx; Linux kernel `7.0.0-42-generic`, `x86_64`, Python `3.12.3`
- Sandbox binary: `/usr/bin/bwrap`, Bubblewrap `0.9.0-1ubuntu0.3`, SHA-256 `e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71`
- Fixed resource ceilings: 4 CPU seconds, 256 MiB address space, 32 open files, 4 processes, zero file-size/core-dump allowance; 8 seconds per sample and 60 seconds per campaign
- Evidence directory: `~/.local/state/ihra/synthetic-experiments/51c30b4d473d4621a32a08e8b7c403d2/`
- All 12 manifest entries verified; SHA-256 of `SHA256SUMS.txt`: `9a620f63252c3d4500bcee5e2ec453e56987fb633360aafb749c17bec955024a`

The timeout control is a separate failed campaign and does not alter the
successful campaign. It has four verified manifest entries; SHA-256 of its
`SHA256SUMS.txt` is
`f208bee375666c56429b84f3edb9759ec9d43631d21c6cb7aaab14d471f97a4b`.
Both campaigns use the same fixed plan and runner identity.

## Scope and limitations

The live campaign demonstrates sequence autonomy and contract analysis for
this one synthetic CPU workload. The real timeout check exercises one
Bubblewrap failure path. The other adversarial controls are unit-level or
mocked parent-validation checks as identified above. In particular, NC-10
does not demonstrate recovery after `SIGKILL`, power loss, or other abrupt
parent termination; such an interruption may leave a campaign marked
`RUNNING`, which must not be interpreted as complete.

No campaign searched for performance improvements or changed CPU/GPU
settings, firmware, drivers, power profiles, or other hardware state. The
original campaign above predates the TTY-consent change and used the earlier
explicit-digest CLI as its local start action. It therefore demonstrates
workload repeatability, not the current consent mechanism. The TTY ceremony
is also not authenticated human identity and does not protect against
malicious processes sharing the operator's UID. Autonomous scientific
planning, general authorization, hardware autonomy, and durable recovery
remain outside this result.

## Crash-recovery contract (M0)

The recovery design uses conservative inspection states; they describe the
evidence found on disk and do not claim whether a campaign process is alive:

| Inspection state | Evidence condition | Claim permitted |
|---|---|---|
| `ACTIVE_OR_UNKNOWN` | The last durable campaign record says `RUNNING`, or an observation cannot establish a terminal record. | The campaign is not demonstrated complete. The process may still be active or may have stopped; this inspection does not decide which. |
| `INCOMPLETE` | A terminal failure/interruption is recorded, or required final state, manifest, sample receipt, or linked artifact is absent. A sample with only a `started` marker is not a completed sample. | `NOT_DEMONSTRATED`; previously validated receipts may remain useful evidence, but do not make the campaign successful. |
| `COMPLETED_VALID` | A terminal `COMPLETED` record and `DEMONSTRATED` verdict are consistent with the fixed plan and proposal; all five ordered sample receipts pass the contract; their plan/campaign links and output digests match; and every required artifact is covered by a consistent manifest. | `DEMONSTRATED` for fixed-workload repeatability only. This does not establish authorship, human identity, or resistance to same-UID modification. |

Any malformed, contradictory, unreadable, or unverifiable evidence is
`NOT_DEMONSTRATED`, never repaired or upgraded by inspection. Missing final
state or a missing manifest is not success. Age, timestamps, or a missing PID
must not be used to infer that a campaign is abandoned or that its owner died.
Inspection must not modify campaign files, finalize a `RUNNING` campaign, or
resume/retry work. The writer emits atomic campaign checkpoints and
incrementally refreshed manifests; the read-only inspector is implemented,
but abrupt process-kill testing is pending. Unit tests do not demonstrate
abrupt process-kill behavior.

`python3 -m core.cli inspect-synthetic-campaign <campaign-id>` validates the
directory without starting an executor or process. It rejects symlinked paths
and files, non-private or non-regular artifacts, malformed/duplicate manifest
entries, missing files, digest mismatches, and inconsistent plan/proposal/
checkpoint/sample links. `ACTIVE_OR_UNKNOWN` is not a claim that a process is
alive; it means the persisted record has not established a terminal outcome.
Historical campaigns without a checkpoint or current proposal binding can be
read and their included receipts checked, but remain
`INCOMPLETE` / `NOT_DEMONSTRATED` under the current contract. The inspector
does not rewrite or migrate those records.

### M2 read-only inspector verification

The M2 implementation adds the `inspect-synthetic-campaign` CLI and a reader
that opens evidence through directory file descriptors without following
symlinks. It validates the fixed plan, proposal/authorization linkage,
terminal checkpoint, ordered start markers and receipts, and exact manifest
coverage. A matching manifest alone is insufficient: a receipt with an
incorrect result digest remains incomplete even after its manifest hash is
updated. Invalid campaign IDs, symlinked path components, duplicate manifest
entries, missing files, unrepresentable durations, and excessively nested JSON
fail closed. The command does not instantiate the campaign runner or launch a
process.

Verification on 2026-11-03: `python3 -m unittest tests.test_campaign_recovery
-v` passed 17 tests; `python3 -m unittest discover -s tests -q` passed 98
tests; `python3 -m compileall -q core tests` and `git diff --check` passed.
The focused recovery suite uses temporary fixtures; it does not exercise a
real abrupt process kill. Eight pre-existing campaign directories were also
read without modification. Their hashes/receipts remained inspectable, but
they were conservatively classified `NOT_DEMONSTRATED` because the historical
artifacts do not satisfy the current proposal/checkpoint linkage (and one
records a runner outside the current pin). No historical evidence was
rewritten. M3's simulated persistence-cut results follow; M4 remains responsible
for a real isolated process-interruption test.

### M3 simulated persistence-cut verification

The M3 harness injects a test-only `BaseException` at 12 writer boundaries:
before the sample-start write; after its replacement but before the manifest;
after the start marker but before the `SAMPLE_RUNNING` checkpoint; during
executor dispatch after that checkpoint; after executor return but before the
receipt write; after receipt replacement but before its manifest update;
before and after the `SAMPLE_RECORDED` checkpoint; before the terminal campaign
record write; after that record's replacement but before its manifest update;
after terminal-checkpoint replacement but before its manifest update; and
after the terminal checkpoint and manifest are written. It then invokes the
disk-only inspector twice as a fresh read operation.
For every case, the classification is deterministic and neither file content,
permissions, modification times, nor directory modification time change.
Receipts already written remain present; any receipt covered by the manifest
retains its matching digest. A leftover atomic-write temporary is separately
verified to remain `INCOMPLETE` and unchanged by inspection.

The campaign remains `ACTIVE_OR_UNKNOWN` when the last durable campaign record
is still `RUNNING`; a replaced terminal record or checkpoint with a stale
manifest is `INCOMPLETE`. If the terminal checkpoint **and its manifest** were
already durably written before the injected stop, the inspector returns
`COMPLETED_VALID`: at that point all completion evidence is present and
verified, even though the runner did not return to its caller. These are
simulated in-process exception cuts, not parent-process termination, storage
failure, or proof of `SIGKILL` handling.

Verification on 2026-11-03: the focused recovery suite passed 19 tests and the
full suite passed 100 tests; `python3 -m compileall -q core tests` and
`git diff --check` passed. No live campaign or system process was interrupted.
Real Linux/Bubblewrap parent termination remains M4 and `NOT_DEMONSTRATED`.

## Proposal and TTY authorization checks

The current CLI accepts a versioned JSON proposal only as inert data. It
rejects unknown keys, duplicate JSON keys, modified workloads or limits,
non-regular files, symlinks at the proposal file, and oversized inputs.
Review emits no authorization and does not invoke the executor. The runner
requires an in-memory one-use grant and re-parses the canonical proposal,
checks that it contains the exact fixed plan, consumes both digests, and
stores `proposal.json` and `plan.json` byte-for-byte in the campaign evidence.

- Full IHRA suite: 78 tests passed after the final hardening changes.
- Piped-stdin check: `run-synthetic-experiment --proposal ...` returned
  `DENY: No controlling TTY is available` even when stdin contained the exact
  `AUTHORIZE <plan-sha256>` phrase; the campaign executor was not invoked.
- Current CLI-to-Bubblewrap campaign: `9cb0377f48a440e3a516e2f6447488ed`,
  `COMPLETED` / `DEMONSTRATED`, 5/5 samples. The command ran under a real
  Linux PTY and Bubblewrap, but an automated PTY test driver supplied the
  confirmation string. This is **not evidence that a human typed or approved
  it**, nor proof of authenticated identity.
- Proposal SHA-256:
  `7f9d95db51f0847ebbaac59f5d9b9cc6091020cff42c40011627140d53b8a263`.
- Plan SHA-256:
  `135907eb2bf31e57a60ffe0a414a6fcb7b0a8cd55c744ae9984e683e61efa010`.
- Evidence directory:
  `~/.local/state/ihra/synthetic-experiments/9cb0377f48a440e3a516e2f6447488ed/`.
  All 13 manifest entries verified; SHA-256 of `SHA256SUMS.txt`:
  `2c6d572ea7ecd1b930dcf88ee8b7c9b5b5ec9b2fde87ffd90b56ad25f2e03c1b`.

### User-operated manual TTY campaign

The user ran `run-synthetic-experiment` from an interactive terminal and
provided the terminal transcript showing the exact authorization phrase
entered at the TTY prompt. The corresponding local evidence was independently
checked: the campaign record is complete, all five sample receipts pass, and
all manifest entries match their SHA-256 hashes.

- Campaign: `d70a7bad893641a89d22f43dd5fa446a`
- State: `COMPLETED` / `DEMONSTRATED`, 5 of 5 samples
- Authorization record: `local_tty_confirmation`; this records the CLI path,
  not cryptographic proof of the identity or physical action of the operator.
- Proposal SHA-256:
  `7f9d95db51f0847ebbaac59f5d9b9cc6091020cff42c40011627140d53b8a263`.
- Plan SHA-256:
  `135907eb2bf31e57a60ffe0a414a6fcb7b0a8cd55c744ae9984e683e61efa010`.
- Result SHA-256 for all five samples:
  `40c778e38799bb8b101d4bb6af933608069274c553be25f62398fa852030ad68`.
- Evidence directory:
  `~/.local/state/ihra/synthetic-experiments/d70a7bad893641a89d22f43dd5fa446a/`.
  All 13 manifest entries verified; SHA-256 of `SHA256SUMS.txt`:
  `6ede40026a4751fbc84de3ecfecef0bf4ba150697f570a624df5de199acfe475`.

The grant prevents accidental API bypass and reuse within this process;
Python objects and module internals are not a security boundary against
arbitrary code running as the same user. The controlling TTY ceremony
expresses local operator consent only. The manual interaction is evidenced by
the user-provided terminal transcript; the campaign artifacts alone cannot
prove who entered the confirmation.
