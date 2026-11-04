# SPDX-License-Identifier: MIT
"""The cpu-boost experiment and smart-boost recipe, tested without root or hardware changes."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXP = ROOT / "experiments" / "cpu-boost"
RECIPE = ROOT / "recipes" / "smart-boost"


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

    def test_uninstall_removes_only_what_install_adds(self):
        install = (RECIPE / "install.sh").read_text()
        uninstall = (RECIPE / "uninstall.sh").read_text()
        removed = [l for l in uninstall.splitlines() if l.lstrip().startswith("rm ")]
        self.assertEqual(len(removed), 1)
        for path in removed[0].split()[2:]:
            self.assertIn(path, install)
        self.assertNotIn("rm ", install)


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


if __name__ == "__main__":
    unittest.main()
