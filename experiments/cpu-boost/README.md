# Experiment: does CPU boost actually help on *your* machine?

A fair, counterbalanced on/off test of the CPU boost switch. It is the experiment that
showed a "5% ceiling" was really a disabled turbo ([case study](../../docs/HISTORY_REA_CPU_01.md)).

```bash
cd experiments/cpu-boost
gcc -O2 -march=native -fopenmp -o cpubench cpubench.c
sudo python3 run_boost_matrix.py          # ~3-5 min, charger connected
```

**What it changes:** only the boost switch (`cpufreq/boost` or Intel `no_turbo`), and it always puts
it back the way it was, even on Ctrl+C. **What it measures:** single-thread and all-thread integer and
vector math, memory bandwidth, package watts (if your kernel exposes RAPL), peak clock and temperature.
Every result has a hash: if a hash changes between boost on/off the run is invalid (the CPU computed
something different). It refuses to start on battery and stops if the charger is unplugged.

**It must be the only thing touching boost while it runs.** It reads the switch back before and after
every job and stops if anything else flipped it (tlp, auto-cpufreq, the [smart-boost recipe](../../recipes/smart-boost/README.md)…):
a run labelled "boost off" that really ran with turbo on would hide the gain. If it finds such a tool
installed it refuses to start; stop that tool for the run, or pass `--allow-other-owner` (the readback
still guards every job). The switch is restored on Ctrl+C, `kill`, and when the terminal or SSH
session closes. A benchmark that crashes is reported as FAILED and the rest of the matrix continues.

Results land in `~/.local/state/ihra/cpu-boost/matrix_<time>.jsonl`; give that file to your agent.

**Reference result** (HP Victus, Ryzen 5 8645HS, on AC): +16% single-thread, +8-9% all-core,
memory unchanged, +38-63% energy per operation, up to 86 °C. On battery: all-core gain ~0%.
Your numbers will differ. That's the point of measuring.

> The `fma` test uses 8 independent vector chains with different seeds. An earlier version used
> identical chains and the compiler merged them, reporting about twice the physical peak. If a
> number looks too good, check it against the hardware's theoretical maximum.
