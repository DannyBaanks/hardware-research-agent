#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
CPU boost on/off matrix (counterbalanced 0-1-1-0-0-1). Linux, AMD or Intel.

    gcc -O2 -march=native -fopenmp -o cpubench cpubench.c
    sudo python3 run_boost_matrix.py

What it touches: ONLY the CPU boost switch (/sys/devices/system/cpu/cpufreq/boost, or
intel_pstate/no_turbo), and it ALWAYS restores the original value on exit, Ctrl+C or error.
Everything else is read-only: clocks, temperature, RAPL package energy (if exposed).
It refuses to start on battery and stops if the charger is unplugged mid-run.
It reads the switch back before and after every job and stops if anything else changed it
(tlp, auto-cpufreq, a udev rule such as recipes/smart-boost...): a mislabelled run would push the
measured gain toward 0%. It refuses to start while such a runtime owner is installed
(override with --allow-other-owner; the readback still guards every job).
Results go to ~/.local/state/ihra/cpu-boost/ of the user who ran sudo.
"""
import json, os, signal, subprocess, sys, threading, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from core.cpu_power import boost_owners, boost_switch, on_ac_power  # noqa: E402

BENCH = HERE / "cpubench"
ORDER = [0, 1, 1, 0, 0, 1]
JOBS = [["int", "1"], ["int", "ALL"], ["fma", "1"], ["fma", "ALL"], ["triad", "ALL"]]
COOL_C = 60.0


def read(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return None


def boost_knob():
    """(path, value_for_on, value_for_off), shared with core/cpu_power.py."""
    path, on, off = boost_switch()
    return (Path(path) if path else None), on, off


class BoostChanged(RuntimeError):
    """Someone else wrote the boost switch during the run."""


def check_switch(knob, expected, when):
    got = read(knob)
    if got != expected:
        raise BoostChanged(f"boost switch is {got!r}, expected {expected!r} {when}")


def cpu_temp():
    for h in Path("/sys/class/hwmon").glob("hwmon*"):
        if read(h / "name") in ("k10temp", "coretemp", "zenpower"):
            v = read(h / "temp1_input")
            if v:
                return int(v) / 1000
    return None


def rapl():
    v = read("/sys/class/powercap/intel-rapl:0/energy_uj")
    return int(v) if v else None


def max_freq_mhz():
    vals = [int(read(p) or 0) for p in Path("/sys/devices/system/cpu/cpufreq").glob("policy*/scaling_cur_freq")]
    return max(vals) // 1000 if vals else None


def cool(timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = cpu_temp()
        if t is None or t <= COOL_C:
            return t
        time.sleep(2)
    return cpu_temp()


def run_job(job):
    """Run one benchmark. Never raises on a benchmark failure: returns a row with "error"."""
    threads = str(os.cpu_count() or 1) if job[1] == "ALL" else job[1]
    peak = [0]
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], max_freq_mhz() or 0)
            time.sleep(0.1)
    th = threading.Thread(target=sample, daemon=True); th.start()
    e0, t0 = rapl(), time.time()
    try:
        out = subprocess.run([str(BENCH), job[0], threads], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        stop.set(); th.join()
        return {"mode": job[0], "threads": int(threads), "error": "timeout after 300 s"}
    t1, e1 = time.time(), rapl()
    stop.set(); th.join()
    try:
        if out.returncode != 0:
            raise ValueError(f"exit {out.returncode}")
        r = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        return {"mode": job[0], "threads": int(threads), "exit": out.returncode,
                "error": f"{e}; stderr: {out.stderr.strip()[-200:]}"}
    if e0 is not None and e1 is not None:
        if e1 < e0:
            e1 += int(read("/sys/class/powercap/intel-rapl:0/max_energy_range_uj") or 0)
        r["pkg_watts"] = round((e1 - e0) / 1e6 / (t1 - t0), 2)
    r.update({"exit": out.returncode, "peak_mhz": peak[0], "temp_after_c": cpu_temp()})
    return r


def summarize(rows):
    print("\n=== Summary (median per state) ===")
    keys = {"int": "Gops_s", "fma": "GFLOP_s", "triad": "GB_s"}
    failed = [r for r in rows if "error" in r]
    rows = [r for r in rows if "error" not in r]
    fmt = lambda v: f"{v:9.3f}" if v is not None else "      n/a"
    for mode, thr in sorted({(r["mode"], r["threads"]) for r in rows}):
        k = keys[mode]
        med = {}
        for b in (0, 1):
            v = sorted(r[k] for r in rows if r["mode"] == mode and r["threads"] == thr and r["boost"] == b)
            med[b] = v[len(v) // 2] if v else None
        hashes = {r["hash"] for r in rows if r["mode"] == mode and r["threads"] == thr}
        gain = 100 * (med[1] / med[0] - 1) if med[0] and med[1] else float("nan")
        ok = "hash OK" if len(hashes) == 1 else "HASH MISMATCH -> invalid"
        print(f"{mode:5} x{thr:<3} boost off {fmt(med[0])}  on {fmt(med[1])}  {k:7}  gain {gain:+6.1f}%  {ok}")
    if failed:
        print(f"\n{len(failed)} run(s) FAILED and are excluded above:")
        for r in failed:
            print(f"  block {r.get('block', '?')} boost={r.get('boost', '?')} {r['mode']} x{r['threads']}: {r['error']}")


# SIGHUP = terminal or SSH session closed: without it the process dies with turbo still off.
RESTORE_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)


def restore_on_signals(restore):
    def on_signal(signum, _frame):
        restore()
        sys.exit(128 + signum)
    for sig in RESTORE_SIGNALS:
        signal.signal(sig, on_signal)


def make_dirs(path, uid, gid):
    """mkdir -p, then give every directory it created to the sudo user (not root)."""
    created = []
    p = path
    while not p.exists():
        created.append(p)
        p = p.parent
    path.mkdir(parents=True, exist_ok=True)
    for d in reversed(created):
        os.chown(d, uid, gid)
    return created


def main():
    if os.geteuid() != 0:
        sys.exit("run with: sudo python3 run_boost_matrix.py")
    if not BENCH.exists():
        sys.exit("build first: gcc -O2 -march=native -fopenmp -o cpubench cpubench.c")
    knob, on, off = boost_knob()
    if knob is None or on is None or off is None:
        sys.exit("no CPU boost switch found (cpufreq/boost or intel_pstate/no_turbo)")
    if on_ac_power() is False and "--allow-battery" not in sys.argv:
        sys.exit("STOP: on battery. Plug in the charger (or pass --allow-battery on purpose).")
    owners = boost_owners()
    for o in owners:
        print(f"note: {o['owner']} also writes the boost switch"
              f" ({'can change it during the run' if o['runtime'] else 'at boot only'})")
    if any(o["runtime"] for o in owners) and "--allow-other-owner" not in sys.argv:
        sys.exit("STOP: something above can flip boost mid-run and mislabel results. Stop it for the run\n"
                 "(e.g. sudo systemctl stop tlp; or uninstall recipes/smart-boost), or pass\n"
                 "--allow-other-owner: every job is still read back and the run stops on any change.")
    uid, gid = int(os.environ.get("SUDO_UID", 0)), int(os.environ.get("SUDO_GID", 0))
    home = Path(os.path.expanduser(f"~{os.environ.get('SUDO_USER', 'root')}"))
    out_dir = home / ".local/state/ihra/cpu-boost"
    make_dirs(out_dir, uid, gid)
    out = out_dir / f"matrix_{time.strftime('%Y%m%dT%H%M%S')}.jsonl"
    original = knob.read_text().strip()
    ac_at_start = on_ac_power()

    def restore(*_):
        knob.write_text(original)
        print(f"\nboost switch restored ({knob} = {knob.read_text().strip()})")
    restore_on_signals(restore)
    rows = []
    try:
        with out.open("w") as f:
            f.write(json.dumps({"env": {
                "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kernel": os.uname().release,
                "cpu": next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), "?"),
                "boost_knob": str(knob), "original": original, "on_ac": ac_at_start,
                "platform_profile": read("/sys/firmware/acpi/platform_profile"),
                "other_boost_owners": owners,
            }}) + "\n")
            for block, b in enumerate(ORDER, 1):
                want = on if b else off
                knob.write_text(want); time.sleep(2)
                for job in JOBS:
                    t = cool()
                    check_switch(knob, want, "before the job")
                    r = run_job(job)
                    r.update({"block": block, "boost": b, "start_temp_c": t, "on_ac": on_ac_power()})
                    try:
                        check_switch(knob, want, "after the job")
                    except BoostChanged:
                        r["error"] = "boost switch changed during the job; label unreliable"
                        f.write(json.dumps(r) + "\n"); f.flush(); rows.append(r)
                        raise
                    f.write(json.dumps(r) + "\n"); f.flush(); rows.append(r)
                    if "error" in r:
                        print(f"block {block} boost={b} {r['mode']:5} x{r['threads']:<3} FAILED: {r['error']}")
                    else:
                        print(f"block {block} boost={b} {r['mode']:5} x{r['threads']:<3} "
                              f"{r.get('Gops_s', r.get('GFLOP_s', r.get('GB_s')))}  {r.get('pkg_watts', '?')} W  "
                              f"peak {r['peak_mhz']} MHz  {r['temp_after_c']} C")
                    if ac_at_start and r["on_ac"] is False:
                        sys.exit("STOP: charger unplugged mid-run; results are not comparable. Start again.")
    except BoostChanged as e:
        sys.exit(f"STOP: {e}. Another tool (tlp, auto-cpufreq, smart-boost...) owns the switch;\n"
                 f"stop it for the run and start again. Partial results kept in {out}")
    finally:
        restore()
        if out.exists():
            os.chown(out, uid, gid)
    summarize(rows)
    print(f"\nRaw results: {out}\nGive this file to your agent: it has every run, watts, clocks and hashes.")


if __name__ == "__main__":
    main()
