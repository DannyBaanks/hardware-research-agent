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
Results go to ~/.local/state/ihra/cpu-boost/ of the user who ran sudo.
"""
import json, os, signal, subprocess, sys, threading, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
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
    """(path, value_for_on, value_for_off)"""
    if read("/sys/devices/system/cpu/cpufreq/boost") in ("0", "1"):
        return Path("/sys/devices/system/cpu/cpufreq/boost"), "1", "0"
    if read("/sys/devices/system/cpu/intel_pstate/no_turbo") in ("0", "1"):
        return Path("/sys/devices/system/cpu/intel_pstate/no_turbo"), "0", "1"
    return None, None, None


def on_ac():
    for ps in Path("/sys/class/power_supply").glob("*"):
        if read(ps / "type") in ("Mains", "USB", "USB_PD", "USB_C") and read(ps / "online") == "1":
            return True
    return not any(read(ps / "type") == "Battery" for ps in Path("/sys/class/power_supply").glob("*"))


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
    threads = str(os.cpu_count()) if job[1] == "ALL" else job[1]
    peak = [0]
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], max_freq_mhz() or 0)
            time.sleep(0.1)
    th = threading.Thread(target=sample, daemon=True); th.start()
    e0, t0 = rapl(), time.time()
    out = subprocess.run([str(BENCH), job[0], threads], capture_output=True, text=True, timeout=300)
    t1, e1 = time.time(), rapl()
    stop.set(); th.join()
    r = json.loads(out.stdout.strip().splitlines()[-1])
    if e0 is not None and e1 is not None:
        if e1 < e0:
            e1 += int(read("/sys/class/powercap/intel-rapl:0/max_energy_range_uj") or 0)
        r["pkg_watts"] = round((e1 - e0) / 1e6 / (t1 - t0), 2)
    r.update({"exit": out.returncode, "peak_mhz": peak[0], "temp_after_c": cpu_temp()})
    return r


def summarize(rows):
    print("\n=== Summary (median per state) ===")
    keys = {"int": "Gops_s", "fma": "GFLOP_s", "triad": "GB_s"}
    for mode, thr in sorted({(r["mode"], r["threads"]) for r in rows}):
        k = keys[mode]
        med = {}
        for b in (0, 1):
            v = sorted(r[k] for r in rows if r["mode"] == mode and r["threads"] == thr and r["boost"] == b)
            med[b] = v[len(v) // 2] if v else None
        hashes = {r["hash"] for r in rows if r["mode"] == mode and r["threads"] == thr}
        gain = 100 * (med[1] / med[0] - 1) if med[0] and med[1] else float("nan")
        ok = "hash OK" if len(hashes) == 1 else "HASH MISMATCH -> invalid"
        print(f"{mode:5} x{thr:<3} boost off {med[0]:9.3f}  on {med[1]:9.3f}  {k:7}  gain {gain:+6.1f}%  {ok}")


def main():
    if os.geteuid() != 0:
        sys.exit("run with: sudo python3 run_boost_matrix.py")
    if not BENCH.exists():
        sys.exit("build first: gcc -O2 -march=native -fopenmp -o cpubench cpubench.c")
    knob, on, off = boost_knob()
    if knob is None or on is None or off is None:
        sys.exit("no CPU boost switch found (cpufreq/boost or intel_pstate/no_turbo)")
    if not on_ac() and "--allow-battery" not in sys.argv:
        sys.exit("STOP: on battery. Plug in the charger (or pass --allow-battery on purpose).")
    uid, gid = int(os.environ.get("SUDO_UID", 0)), int(os.environ.get("SUDO_GID", 0))
    home = Path(os.path.expanduser(f"~{os.environ.get('SUDO_USER', 'root')}"))
    out_dir = home / ".local/state/ihra/cpu-boost"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"matrix_{time.strftime('%Y%m%dT%H%M%S')}.jsonl"
    original = knob.read_text().strip()
    ac_at_start = on_ac()

    def restore(*_):
        knob.write_text(original)
        print(f"\nboost switch restored ({knob} = {knob.read_text().strip()})")
    signal.signal(signal.SIGINT, lambda *a: (restore(), sys.exit(130)))
    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(143)))
    rows = []
    try:
        with out.open("w") as f:
            f.write(json.dumps({"env": {
                "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kernel": os.uname().release,
                "cpu": next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), "?"),
                "boost_knob": str(knob), "original": original, "on_ac": ac_at_start,
                "platform_profile": read("/sys/firmware/acpi/platform_profile"),
            }}) + "\n")
            for block, b in enumerate(ORDER, 1):
                knob.write_text(on if b else off); time.sleep(2)
                for job in JOBS:
                    t = cool()
                    r = run_job(job)
                    r.update({"block": block, "boost": b, "start_temp_c": t, "on_ac": on_ac()})
                    f.write(json.dumps(r) + "\n"); f.flush(); rows.append(r)
                    print(f"block {block} boost={b} {r['mode']:5} x{r['threads']:<3} "
                          f"{r.get('Gops_s', r.get('GFLOP_s', r.get('GB_s')))}  {r.get('pkg_watts', '?')} W  "
                          f"peak {r['peak_mhz']} MHz  {r['temp_after_c']} C")
                    if ac_at_start and not r["on_ac"]:
                        sys.exit("STOP: charger unplugged mid-run; results are not comparable. Start again.")
    finally:
        restore()
        os.chown(out, uid, gid)
    summarize(rows)
    print(f"\nRaw results: {out}\nGive this file to your agent: it has every run, watts, clocks and hashes.")


if __name__ == "__main__":
    main()
