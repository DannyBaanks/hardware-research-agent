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
plan digest is an explicit local start action, not authenticated human
identity. Autonomous planning, general authorization, hardware autonomy,
and durable recovery remain outside this result.
