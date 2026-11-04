# SPDX-License-Identifier: MIT
"""The cpu-boost experiment and smart-boost recipe, tested without root or hardware changes."""
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXP = ROOT / "experiments" / "cpu-boost"
RECIPE = ROOT / "recipes" / "smart-boost"
sys.path.insert(0, str(ROOT))
from core.cpu_power import boost_owners, on_ac_power  # noqa: E402


def _w(root, rel, text):
    path = Path(root) / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")


# name -> {supply: {attr: value}}; expected on_ac_power() in the last element.
POWER_CASES = {
    "charger online": ({"ACAD": {"type": "Mains", "online": "1"}, "BAT0": {"type": "Battery"}}, True),
    "charger offline": ({"ACAD": {"type": "Mains", "online": "0"}, "BAT0": {"type": "Battery"}}, False),
    "usb-c only": ({"ACAD": {"type": "Mains", "online": "0"}, "ucsi": {"type": "USB", "online": "1"}}, True),
    "desktop, nothing": ({}, None),
    "desktop + wireless mouse": ({"hidpp_battery_0": {"type": "Battery", "scope": "Device",
                                                       "status": "Discharging"}}, None),
    "battery only, discharging": ({"BAT0": {"type": "Battery", "status": "Discharging"}}, False),
    "battery only, charging": ({"BAT0": {"type": "Battery", "status": "Charging"}}, True),
}


def _power_tree(root, supplies):
    (Path(root) / "sys/class/power_supply").mkdir(parents=True, exist_ok=True)
    for name, attrs in supplies.items():
        for k, v in attrs.items():
            _w(root, f"/sys/class/power_supply/{name}/{k}", v)


def _load_matrix():
    spec = importlib.util.spec_from_file_location("run_boost_matrix", EXP / "run_boost_matrix.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SmartBoostRecipe(unittest.TestCase):
    def test_fake_sysfs_cases_pass(self):
        res = subprocess.run(["bash", str(RECIPE / "test_smart_boost.sh")], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("FAIL", res.stdout)
        self.assertGreaterEqual(res.stdout.count("PASS"), 8)

    def test_scripts_parse(self):
        for name in ("install.sh", "uninstall.sh"):
            self.assertEqual(subprocess.run(["bash", "-n", str(RECIPE / name)]).returncode, 0)
        self.assertEqual(subprocess.run(["sh", "-n", str(RECIPE / "ihra-smart-boost")]).returncode, 0)

    def test_udev_rule_does_not_depend_on_optional_attributes(self):
        # Regression: ATTR{scope}!="Device" never matched on laptops whose ACAD/BAT0 have no
        # scope file (udev fails a match on a missing attribute even with !=). Seen on the
        # reference HP Victus: unplugging the charger did not run the script.
        rule = [l for l in (RECIPE / "90-ihra-smart-boost.rules").read_text().splitlines()
                if l.strip() and not l.startswith("#")]
        self.assertEqual(len(rule), 1)
        self.assertNotIn("ATTR{scope}", rule[0])
        self.assertIn('ATTR{type}=="Mains|USB|USB_PD|USB_C|Battery"', rule[0])

    def test_uninstall_removes_only_what_install_adds(self):
        install = (RECIPE / "install.sh").read_text()
        uninstall = (RECIPE / "uninstall.sh").read_text()
        removed = [l for l in uninstall.splitlines() if l.lstrip().startswith("rm ")]
        self.assertEqual(len(removed), 1)
        for path in removed[0].split()[2:]:
            self.assertIn(path, install)
        self.assertNotIn("rm ", install)


class SharedPowerLogic(unittest.TestCase):
    """core/cpu_power.py is the source of truth; the sh recipe must decide the same way."""

    def test_on_ac_power_cases(self):
        for name, (supplies, want) in POWER_CASES.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                _power_tree(root, supplies)
                self.assertIs(on_ac_power(root), want)

    def test_shell_recipe_matches_python(self):
        expected_boost = {True: {"0": "1", "1": "1"}, False: {"0": "0", "1": "0"},
                          None: {"0": "0", "1": "1"}}  # unknown power: leave it alone
        for name, (supplies, _) in POWER_CASES.items():
            for start in ("0", "1"):
                with self.subTest(name, start=start), tempfile.TemporaryDirectory() as root:
                    _power_tree(root, supplies)
                    _w(root, "/sys/devices/system/cpu/cpufreq/boost", start)
                    subprocess.run(["sh", str(RECIPE / "ihra-smart-boost")], check=True,
                                   env={**os.environ, "SYSFS_ROOT": root})
                    got = (Path(root) / "sys/devices/system/cpu/cpufreq/boost").read_text().strip()
                    self.assertEqual(got, expected_boost[on_ac_power(root)][start])

    def test_boost_owners_separates_runtime_from_boot(self):
        with tempfile.TemporaryDirectory() as root:
            _w(root, "/etc/udev/rules.d/90-ihra-smart-boost.rules", 'RUN+="/usr/local/sbin/ihra-smart-boost"')
            _w(root, "/usr/local/sbin/ihra-smart-boost", (RECIPE / "ihra-smart-boost").read_text())
            # A renamed copy is found by following RUN+= into the script it calls.
            _w(root, "/etc/udev/rules.d/90-other-boost.rules", 'RUN+="/usr/local/sbin/other"')
            _w(root, "/usr/local/sbin/other", "echo 0 > /sys/devices/system/cpu/cpufreq/boost")
            _w(root, "/etc/tmpfiles.d/boost.conf", "w /sys/devices/system/cpu/cpufreq/boost - - - - 0")
            _w(root, "/etc/udev/rules.d/50-unrelated.rules", 'SUBSYSTEM=="usb"')
            owners = {o["owner"]: o["runtime"] for o in boost_owners(root)}
        self.assertEqual(owners, {"/etc/udev/rules.d/90-ihra-smart-boost.rules": True,
                                  "/etc/udev/rules.d/90-other-boost.rules": True,
                                  "/etc/tmpfiles.d/boost.conf": False})


class BoostMatrix(unittest.TestCase):
    def test_refuses_without_root(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        res = subprocess.run(["python3", str(EXP / "run_boost_matrix.py")], capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("sudo", res.stderr)

    def test_summary_flags_hash_mismatch(self):
        mod = _load_matrix()
        rows = [
            {"mode": "int", "threads": 1, "boost": 0, "Gops_s": 1.0, "hash": "a"},
            {"mode": "int", "threads": 1, "boost": 1, "Gops_s": 1.2, "hash": "a"},
            {"mode": "fma", "threads": 1, "boost": 0, "GFLOP_s": 10.0, "hash": "x"},
            {"mode": "fma", "threads": 1, "boost": 1, "GFLOP_s": 99.0, "hash": "y"},
        ]
        out = StringIO()
        with redirect_stdout(out):
            mod.summarize(rows)
        text = out.getvalue()
        self.assertIn("+20.0%", text)
        self.assertIn("HASH MISMATCH", text)

    def test_summary_skips_failed_runs_and_missing_states(self):
        mod = _load_matrix()
        rows = [
            {"mode": "int", "threads": 1, "boost": 0, "Gops_s": 1.0, "hash": "a"},
            {"mode": "int", "threads": 1, "boost": 1, "error": "exit 139", "block": 2},
            {"mode": "fma", "threads": 1, "boost": 1, "GFLOP_s": 5.0, "hash": "b"},
        ]
        out = StringIO()
        with redirect_stdout(out):
            mod.summarize(rows)
        text = out.getvalue()
        self.assertIn("n/a", text)
        self.assertIn("1 run(s) FAILED", text)
        self.assertIn("exit 139", text)

    def test_failed_benchmark_returns_an_error_row_instead_of_raising(self):
        mod = _load_matrix()
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "cpubench"
            fake.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
            fake.chmod(0o755)
            with mock.patch.object(mod, "BENCH", fake):
                row = mod.run_job(["int", "1"])
        self.assertIn("error", row)
        self.assertEqual(row["exit"], 3)
        self.assertIn("boom", row["error"])

    def test_readback_detects_a_switch_changed_by_someone_else(self):
        mod = _load_matrix()
        with tempfile.TemporaryDirectory() as tmp:
            knob = Path(tmp) / "boost"
            knob.write_text("0\n")
            mod.check_switch(knob, "0", "before")
            knob.write_text("1\n")  # e.g. smart-boost's udev rule on a charger event
            with self.assertRaises(mod.BoostChanged):
                mod.check_switch(knob, "0", "after")

    def test_output_dirs_are_given_back_to_the_sudo_user(self):
        mod = _load_matrix()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".local/state/ihra/cpu-boost"
            (Path(tmp) / ".local").mkdir()  # already existed: must not be touched
            with mock.patch.object(mod.os, "chown") as chown:
                created = mod.make_dirs(target, 1234, 5678)
            self.assertTrue(target.is_dir())
        chowned = [c.args[0] for c in chown.call_args_list]
        self.assertEqual(chowned, [Path(tmp) / ".local/state", Path(tmp) / ".local/state/ihra", target])
        self.assertEqual(sorted(created), sorted(chowned))
        self.assertTrue(all(c.args[1:] == (1234, 5678) for c in chown.call_args_list))

    def test_switch_is_restored_when_the_terminal_closes(self):
        for sig in (signal.SIGHUP, signal.SIGQUIT, signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig.name), tempfile.TemporaryDirectory() as tmp:
                marker = Path(tmp) / "restored"
                code = (
                    "import importlib.util, pathlib, sys, time\n"
                    f"spec = importlib.util.spec_from_file_location('m', {str(EXP / 'run_boost_matrix.py')!r})\n"
                    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                    f"m.restore_on_signals(lambda: pathlib.Path({str(marker)!r}).write_text('1'))\n"
                    "print('ready', flush=True)\n"
                    "time.sleep(30)\n"
                )
                proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
                self.assertEqual(proc.stdout.readline().strip(), "ready")
                proc.send_signal(sig)
                rc = proc.wait(timeout=10)
                proc.stdout.close()
                self.assertTrue(marker.exists(), f"{sig.name} did not restore the switch")
                self.assertEqual(rc, 128 + sig)

    @unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
    def test_benchmark_builds_and_reports_a_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "cpubench"
            build = subprocess.run(["gcc", "-O2", "-march=native", "-fopenmp", "-o", str(exe),
                                    str(EXP / "cpubench.c")], capture_output=True, text=True)
            if build.returncode != 0:
                self.skipTest("toolchain cannot build OpenMP: " + build.stderr[-200:])
            res = subprocess.run([str(exe), "triad", "2"], capture_output=True, text=True, timeout=120)
            self.assertEqual(res.returncode, 0)
            data = json.loads(res.stdout)
            self.assertEqual(data["mode"], "triad")
            self.assertRegex(data["hash"], r"^[0-9a-f]{16}$")
            self.assertGreater(data["GB_s"], 0)
            # Thread count is validated (results array is sized from it, no fixed 64 limit).
            for bad in ("0", "-3", "abc"):
                self.assertEqual(subprocess.run([str(exe), "int", bad], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
