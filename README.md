# IHRA — let an AI agent help tune your Linux PC, with proof instead of hype

[Español](README_es.md) · [Technical reference](docs/TECHNICAL.md) · [Safety contracts](docs/SAFETY_CONTRACTS.md) · [Case studies](#the-story-behind-it)

**IHRA (ISyCo Hardware Research Agent)** is a small toolkit plus a rulebook for your AI coding agent (Claude Code, Codex, opencode, Gemini CLI, …). With it, the agent can look at your hardware, measure it, and suggest improvements the way a careful scientist would. **You** stay in charge of every change.

It exists because "optimization tips" on the internet are often wrong, and an AI agent can sound very sure about a tweak that does nothing, or makes things worse. IHRA makes the agent **prove** a gain before anyone calls it one.

> [!IMPORTANT]
> IHRA is a **research prototype**, not a one-click booster. On its own it only **reads** your system and runs harmless test workloads. It never overclocks, flashes firmware or changes settings by itself. "We measured it and there is no safe gain" is a perfectly good result.

## The story behind it

IHRA came out of real sessions where AI agents helped tune one HP Victus laptop (Ryzen 5 8645HS + RTX 4050 Laptop):

- **The GPU that lied.** An experimental unlock made the NVIDIA driver *report* full-speed tensor math, yet the real speed had silently dropped back to stock. The laptop's power-saving mode (`D3cold`) was cutting power to the GPU and resetting it. The only way to catch this was to measure real throughput instead of trusting what the software reported. Once the cause was found and fixed, a tensor benchmark ran at about **2× the stock speed** in 5 out of 5 repeat runs, and real matrix math (cuBLAS) got **+36% to +50%** faster. → [case study](docs/HISTORY_ADA_D3COLD.md)
- **The "5% ceiling" that was a switched-off turbo.** A first CPU study concluded about +5% was all this laptop had left. A second look found the CPU **turbo had been disabled** by a config file another agent had added, the benchmark was Python threads stuck on a single core, and other programs were running in the background. Measured properly, turbo on gave **+16% on one core and +8–9% on all cores** while plugged in, and almost nothing on battery. So the owner went with a "smart boost": on with the charger, off on battery. → [case study](docs/HISTORY_REA_CPU_01.md)

Both stories teach the same lesson: **what the system reports is not always what the hardware is doing.** Those are results from **one machine**. They are not promises for yours. The point is the *method*: look for hidden switches, measure first, compare fairly, and be honest about the result. That method is what IHRA packages.

## How to use it with your AI agent

**You need:** Linux, `git` and Python 3 (tested on 3.12). Node.js is optional.

**1. Get it**

```bash
git clone https://github.com/DannyBaanks/hardware-research-agent.git
cd hardware-research-agent
python3 -m unittest discover -s tests   # optional: should end with "OK"
```

**2. See what it finds on your PC** (read-only, changes nothing)

```bash
python3 -m core.cli --text discover
```

You will see your CPU, power profile, GPU and NPU (if you have them). Drop `--text` to get JSON, which is easier for an agent to read.

**3. Open your AI agent inside this folder and paste this prompt:**

```text
You are helping me research performance on my Linux PC using IHRA (this repo).
First read AGENTS.md and README.md, then run: python3 -m core.cli discover
Start with any "Things to check" hints it prints (disabled turbo, battery,
background load) before benchmarking anything.

Rules:
- Only run read-only commands on your own. Ask me before ANY change to my system,
  and tell me exactly how to undo it before I make it.
- Never flash firmware or BIOS, write MSR/SMU registers, or disable thermal limits.
- Measure before and after at least 3 times each, with the same workload, and
  check the results are still correct, not just faster.
- Don't trust what software reports on its own; confirm with real measurements.
- If you can't prove a gain, say NOT_DEMONSTRATED or NO_SAFE_GAIN_FOUND.

My goal: <for example "faster local AI models", "quieter fans", "better battery life">
```

From there the agent can explain your hardware, propose a plan, and help you measure each change. You decide what actually gets applied.

## What IHRA does, and what it doesn't

| ✅ IHRA does | 🚫 IHRA does not |
|---|---|
| Report your CPU, power profile, GPU and NPU (`discover`) | Change any setting by itself |
| Warn about hidden switches: turbo disabled, on battery, power-saving profile, background load | Guess why a setting was changed; it tells you where to look |
| Check whether a command is on its read-only allowlist (`sentinel`) | Overclock, undervolt, or modify BIOS/firmware |
| Run a fixed, harmless CPU test in a sandbox to check that results repeat | Promise more FPS or a faster PC |
| Reject "faster" claims that lack proper runs, hashes and a control (`AntiFake`) | Let an agent call a single lucky run a win |
| Show NVIDIA Ada status and the D3cold power trap (`ada`) | Install or run the experimental GPU unlock for you |
| Try **one** power-profile change while you type `AUTHORIZE` at this terminal, then prove it went back (`run-tier1`) | Change anything unattended — and if a change can't be proven restored, it refuses every later run until you fix it |

## What the results mean

| Result | In plain words |
|---|---|
| `DEMONSTRATED` | Measured properly and the effect is real *on this machine* |
| `NOT_DEMONSTRATED` | Not enough evidence yet, so don't count it as a gain |
| `NO_SAFE_GAIN_FOUND` | Tested fairly and nothing safe helped. That's still useful to know! |
| `REFUTED` / `DENY` | The claim was false, or the action isn't allowed |

## Handy commands

| Command | What it does |
|---|---|
| `python3 -m core.cli --text discover` | Summary of your hardware |
| `python3 -m core.cli ada` | NVIDIA RTX 40-series status: driver power setting, unlock module, power state |
| `python3 -m core.cli sentinel 'lscpu'` | Is this exact command on the read-only allowlist? (nothing is executed) |
| `python3 -m core.cli dry-run ada_d3cold_false_positive` | Replays the "GPU that lied" case with sample data |
| `python3 -m core.cli run-synthetic sha256-chain-v1` | Harmless sandboxed CPU test (needs `bubblewrap`) |
| `python3 -m core.cli run-synthetic-experiment --proposal <file>` | Five timed samples in the sandbox, after you type `AUTHORIZE <sha256>` in this terminal |
| `python3 -m core.cli inspect-synthetic-campaign <id>` | Read a finished or interrupted campaign back, digest by digest — reruns nothing |
| `python3 -m core.cli run-tier1 --set balanced` | Try one power-profile change with a human watching: applies it, measures it, puts it back, proves it |
| `python3 -m core.cli list-faults` | Is any change still unrestored? Exits non-zero while one remains |
| `python3 -m core.cli resolve-fault <id>` | Prove the machine is back the way it should be, then close that record. Changes nothing itself |
| `python3 -m core.cli inspect <file>` | Analyse a binary with [REA](https://github.com/DannyBaanks/rea), if you have it installed |

## When something goes wrong

**A campaign crashed, was killed, or you pressed Ctrl-C.** Don't rerun it and don't trust the last log
line — read the evidence:

```bash
python3 -m core.cli inspect-synthetic-campaign <campaign-id>
```

It re-checks the manifest file by file and answers `COMPLETED_VALID`, `INCOMPLETE`,
`NOT_DEMONSTRATED` or `ACTIVE_OR_UNKNOWN`. It never resumes anything and never makes an old campaign
look better than it is. Each of the five samples seals its own receipt before the next one starts, so
an interruption costs at most the sample that was running and the evidence says exactly where it
stopped. **Automatic resuming is not implemented** — the checkpoint tells you where it ended, you
decide what to do next.

**A power-profile change did not go back.** The Tier-1 runner says so plainly, prints the commands that
put it back, and writes an emergency fault record. While that record is open, IHRA refuses to run any
further campaign — sandbox included. You fix the machine by hand, then `resolve-fault <id>` proves the
host matches what the record demands and closes it. That is the design: **IHRA stops, it does not
repair.** There is no background service that restores anything when the PC boots.

## Ready-made experiments and recipes

| What | Run it | Changes |
|---|---|---|
| **Does turbo help on my PC?** A fair on/off test with watts, clocks, temperature and hashes | [`experiments/cpu-boost`](experiments/cpu-boost/README.md) | The turbo switch. It puts the switch back at the end and on Ctrl-C — **outside** IHRA's safety gate, see below |
| **Smart boost:** turbo on with charger, off on battery | [`recipes/smart-boost`](recipes/smart-boost/README.md) | Adds 3 files, one of which keeps setting the switch at every boot; `uninstall.sh` removes them |

These need `sudo` because they touch a system setting. Your agent should explain them to you; **you** run them.

> [!WARNING]
> These two are **scripts you run, not IHRA capabilities**. `run_boost_matrix.py` writes
> `/sys/devices/system/cpu/cpufreq/boost` under `sudo`, and `smart-boost` installs a udev rule plus a
> service that keep writing it. They re-read the switch and restore it when the run ends or you
> interrupt it, but they do that on their own: outside the Sentinel gate, outside the `AUTHORIZE`
> ceremony, outside `RollbackContract`, and outside the sealed evidence. IHRA cannot prove their
> rollback for you, and if the machine dies mid-run nothing puts the switch back at boot. Wiring them
> into the same contract is the next milestone (IHRA-05); until then, treat them as hand-run tools.

If you have Node.js, `node bin/ihra.mjs <command>` works too. The full list, with every limit spelled out, is in the [technical reference](docs/TECHNICAL.md#cli-commands-and-their-limits).

## Questions

**Will this make my games or AI models faster?** Maybe, maybe not. IHRA helps you *find out* honestly. Often the answer is a power profile, a driver setting or a cooling fix, and sometimes there's nothing safe to gain.

**Can it break my PC?** IHRA on its own only reads information and runs a sandboxed test. There is now
exactly one thing it can change — a power profile — and only in the same terminal where you typed the
confirmation, after which it re-reads the machine to prove it put things back. The risk comes from
changes **you** approve, which is why the rules above require a way to undo every change, why a change
it cannot prove restored blocks all later runs, and why dangerous categories are banned. Read what your
agent proposes before saying yes.

**Windows or macOS?** Not yet. IHRA reads Linux-specific files (`/proc`, `/sys`).

**Do I need an NVIDIA GPU?** No. The NVIDIA checks only kick in when one is present.

**What about the 2× GPU unlock?** That is a separate experimental kernel project, [gpu-unlock-ada](https://github.com/DannyBaanks/gpu-unlock-ada). It covers specific RTX 40-series cards, is only verified on one laptop, and has real risks. IHRA never installs it.

## For developers

- [Technical reference](docs/TECHNICAL.md): exactly what is implemented, where each guarantee stops, and the known gaps
- [Research pipeline](docs/COMMON_RESEARCH_PIPELINE.md) · [Safety contracts](docs/SAFETY_CONTRACTS.md) · [Architecture](docs/ARCHITECTURE.md) · [Roadmap](ROADMAP.md)
- Tests: `python3 -m unittest discover -s tests -v`

Please don't publish hostnames, serial numbers, GPU UUIDs or tokens in issues or evidence.

**License:** [MIT](LICENSE) · Made by [DannyBaanks](https://github.com/DannyBaanks), together with the AI agents that helped tune the laptop where IHRA was born.
