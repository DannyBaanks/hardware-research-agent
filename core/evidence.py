# SPDX-License-Identifier: MIT
"""Judges measurements that already exist. Reads one file; touches nothing else.

This is the bridge between a measurement someone ran and the AntiFake verdict.
It executes no workload and reads no hardware, so the verdict is only ever about
the numbers in the file: IHRA did not produce them and cannot say where they
came from. Two layouts are understood:

* an IHRA evidence object (the layout of fixtures/*/mock_benchmark.json), which
  may carry executed negative-control runs instead of a declared delta;
* the JSON-lines file written by experiments/cpu-boost/run_boost_matrix.py.
"""

import hashlib
import json
import os
import re
import stat
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .verifier import (
    NEGATIVE_CONTROL_LIMIT,
    AntiFakeVerifier,
    VerificationResult,
    VerificationVerdict,
    negative_control_delta_from_runs,
)

MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
FORMAT_EVIDENCE_JSON = "ihra-evidence-json"
FORMAT_BOOST_MATRIX = "boost-matrix-jsonl"
EVIDENCE_ORIGIN = (
    "caller-supplied file; IHRA did not run these workloads and cannot attest "
    "where the numbers came from"
)

# The boost matrix reports one rate per benchmark mode and a 64-bit FNV-1a result
# hash. That hash catches a computation that changed; it is not tamper evidence.
BOOST_MATRIX_RATE_KEYS = {"int": "Gops_s", "fma": "GFLOP_s", "triad": "GB_s"}
BOOST_MATRIX_DIGEST = re.compile(r"^[0-9a-fA-F]{16}$")
_CONTROL_SELECTOR = re.compile(r"^([a-z0-9_]{1,32}):([0-9]{1,5})$")

_EVIDENCE_FIELDS = {"baseline_runs", "intervention_runs"}
_EVIDENCE_OPTIONAL = {
    "experiment_id",
    "negative_control_delta",
    "control_baseline_runs",
    "control_intervention_runs",
    "software_telemetry",
}


class EvidenceError(ValueError):
    """The file cannot be judged at all, as opposed to judged and found wanting."""


def read_evidence_bytes(path: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise EvidenceError(f"Evidence file cannot be opened: {exc.strerror or exc}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise EvidenceError("Evidence must be a regular file")
        if metadata.st_size > MAX_EVIDENCE_BYTES:
            raise EvidenceError("Evidence exceeds the size limit")
        chunks = []
        remaining = MAX_EVIDENCE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as exc:
        raise EvidenceError(f"Evidence file cannot be read: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("Evidence exceeds the size limit")
    return data


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceError(f"Duplicate evidence key: {key}")
        value[key] = item
    return value


def _reject_constant(name):
    raise EvidenceError(f"Evidence contains a non-finite number: {name}")


def _parse_json(text: str, what: str):
    try:
        return json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except EvidenceError:
        raise
    except (ValueError, RecursionError) as exc:
        raise EvidenceError(f"{what} is not valid JSON") from exc


def _comparison(
    identifier: str,
    result: VerificationResult,
    role: str = "claim",
    excluded_failed_runs: int = 0,
) -> Dict[str, Any]:
    return {
        "id": identifier,
        "role": role,
        "verdict": result.verdict.value,
        "reason": result.reason,
        "median_speedup_factor": result.median_speedup_factor,
        "p_value": result.p_value,
        "confidence": result.confidence,
        "output_hash_invariant": result.output_hash_invariant,
        "negative_control_invariant": result.negative_control_invariant,
        "baseline": result.baseline_statistics,
        "intervention": result.intervention_statistics,
        "excluded_failed_runs": excluded_failed_runs,
    }


def _evaluate_evidence_object(value: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    missing = sorted(_EVIDENCE_FIELDS - set(value))
    unknown = sorted(set(value) - _EVIDENCE_FIELDS - _EVIDENCE_OPTIONAL)
    if missing:
        raise EvidenceError(f"Evidence is missing {', '.join(missing)}")
    if unknown:
        raise EvidenceError(f"Evidence has unknown fields {', '.join(unknown)}")
    telemetry = value.get("software_telemetry", {})
    if not isinstance(telemetry, dict):
        raise EvidenceError("software_telemetry must be an object")
    identifier = value.get("experiment_id", "evidence")
    if not isinstance(identifier, str) or not identifier:
        raise EvidenceError("experiment_id must be a non-empty string")

    has_runs = "control_baseline_runs" in value or "control_intervention_runs" in value
    if has_runs:
        # Executed control runs outrank a declared number: if both are present the
        # declared one is ignored rather than allowed to paper over the runs.
        delta, issue = negative_control_delta_from_runs(
            value.get("control_baseline_runs"), value.get("control_intervention_runs")
        )
        control = {"source": "computed from control runs in the file", "delta": delta}
        if issue:
            control["issue"] = issue
        if "negative_control_delta" in value:
            control["ignored_declared_delta"] = True
    elif "negative_control_delta" in value:
        delta = value["negative_control_delta"]
        control = {
            "source": "declared in the file; not recomputed from runs",
            "delta": delta if isinstance(delta, (int, float)) and not isinstance(delta, bool) else None,
        }
    else:
        delta = None
        control = {"source": "absent", "delta": None}

    result = AntiFakeVerifier().verify_experiment(
        value["baseline_runs"], value["intervention_runs"], telemetry, delta
    )
    return [_comparison(identifier, result)], control


def _matrix_rows(lines: List[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    header = _parse_json(lines[0], "Evidence header line")
    if not isinstance(header, dict) or set(header) != {"env"} or not isinstance(header["env"], dict):
        raise EvidenceError("Evidence is neither an IHRA evidence object nor a boost matrix file")
    rows = []
    for number, line in enumerate(lines[1:], 2):
        row = _parse_json(line, f"Evidence line {number}")
        if not isinstance(row, dict):
            raise EvidenceError(f"Evidence line {number} must be an object")
        mode, threads = row.get("mode"), row.get("threads")
        if (
            not isinstance(mode, str)
            or not re.fullmatch(r"[a-z0-9_]{1,32}", mode)
            or isinstance(threads, bool)
            or not isinstance(threads, int)
            or threads < 1
        ):
            raise EvidenceError(f"Evidence line {number} has no valid mode and thread count")
        if "error" not in row and row.get("boost") not in (0, 1):
            raise EvidenceError(f"Evidence line {number} has no boost state")
        rows.append(row)
    return header["env"], rows


def _matrix_runs(rows, mode, threads, boost) -> List[Dict[str, Any]]:
    key = BOOST_MATRIX_RATE_KEYS.get(mode)
    return [
        {"throughput": row.get(key), "output_sha256": row.get("hash")}
        for row in rows
        if row["mode"] == mode and row["threads"] == threads and row.get("boost") == boost
    ]


def _evaluate_boost_matrix(
    lines: List[str], control_selector: Optional[str]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    env, rows = _matrix_rows(lines)
    failed = Counter((row["mode"], row["threads"]) for row in rows if "error" in row)
    usable = [row for row in rows if "error" not in row]
    groups = sorted({(row["mode"], row["threads"]) for row in rows})

    control_group = None
    if control_selector is not None:
        match = _CONTROL_SELECTOR.fullmatch(control_selector)
        if not match:
            raise EvidenceError("--control must look like MODE:THREADS, for example triad:12")
        control_group = (match.group(1), int(match.group(2)))
        if control_group not in groups:
            available = ", ".join(f"{mode}:{threads}" for mode, threads in groups)
            raise EvidenceError(
                f"--control {control_selector} is not a job in this file (jobs: {available})"
            )

    if control_group is None:
        delta = None
        control = {
            "source": "absent",
            "delta": None,
            "hint": (
                "name a job the intervention should not move with --control MODE:THREADS; "
                "without an executed control no gain can be DEMONSTRATED"
            ),
        }
    else:
        delta, issue = negative_control_delta_from_runs(
            _matrix_runs(usable, *control_group, 0),
            _matrix_runs(usable, *control_group, 1),
            digest_pattern=BOOST_MATRIX_DIGEST,
        )
        control = {
            "source": f"computed from the {control_group[0]}:{control_group[1]} runs in the file",
            "delta": delta,
            "limit": NEGATIVE_CONTROL_LIMIT,
        }
        if issue:
            control["issue"] = issue

    # AF-04: a matrix measured partly on battery compares two different power
    # envelopes, whatever the boost switch said.
    power_sources = {row.get("on_ac") for row in usable}
    mixed_power = len(power_sources) > 1

    verifier = AntiFakeVerifier()
    comparisons = []
    for mode, threads in groups:
        identifier = f"{mode}:{threads}"
        if (mode, threads) == control_group:
            continue
        if mode not in BOOST_MATRIX_RATE_KEYS:
            result = VerificationResult(
                VerificationVerdict.NOT_DEMONSTRATED,
                0.0,
                f"Unknown benchmark mode {mode!r}: no rate field is defined for it",
                1.0,
                False,
                False,
            )
        else:
            result = verifier.verify_experiment(
                _matrix_runs(usable, mode, threads, 0),
                _matrix_runs(usable, mode, threads, 1),
                {},
                delta,
                output_digest_pattern=BOOST_MATRIX_DIGEST,
            )
            if mixed_power and result.verdict is VerificationVerdict.DEMONSTRATED:
                result.verdict = VerificationVerdict.NOT_DEMONSTRATED
                result.reason = (
                    "POWER_SOURCE_MISMATCH: runs in this file were measured on different "
                    "power sources, so the two groups are not comparable"
                )
        comparisons.append(
            _comparison(identifier, result, excluded_failed_runs=failed[(mode, threads)])
        )

    context = {
        "baseline": "boost switch off",
        "intervention": "boost switch on",
        "output_digest": "fnv1a64 result hash: detects a changed computation, not tampering",
        "power_sources_seen": sorted(str(source) for source in power_sources),
        "recorded_environment": {
            key: env.get(key) for key in ("date", "kernel", "cpu", "on_ac", "platform_profile")
        },
    }
    return comparisons, control, context


def evaluate_evidence(data: bytes, control_selector: Optional[str] = None) -> Dict[str, Any]:
    """Returns the verdicts for one evidence file's bytes. Pure: no I/O, no host access."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("Evidence is not valid UTF-8") from exc
    if not text.strip():
        raise EvidenceError("Evidence is empty")

    context = None
    try:
        value = _parse_json(text, "Evidence")
    except EvidenceError as whole_file_error:
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            raise whole_file_error
        evidence_format = FORMAT_BOOST_MATRIX
        comparisons, control, context = _evaluate_boost_matrix(lines, control_selector)
    else:
        if not isinstance(value, dict):
            raise EvidenceError("Evidence must be a JSON object")
        if control_selector is not None:
            raise EvidenceError(
                "--control selects a job in a boost matrix file; an evidence object "
                "carries its own control_baseline_runs / control_intervention_runs"
            )
        evidence_format = FORMAT_EVIDENCE_JSON
        comparisons, control = _evaluate_evidence_object(value)

    if not comparisons:
        raise EvidenceError("Evidence contains nothing to compare besides the control")
    report = {
        "status": "EVALUATED",
        "format": evidence_format,
        "evidence_sha256": hashlib.sha256(data).hexdigest(),
        "evidence_origin": EVIDENCE_ORIGIN,
        "host_accessed": False,
        "commands_executed": 0,
        "negative_control": control,
        "comparisons": comparisons,
        "summary": dict(sorted(Counter(item["verdict"] for item in comparisons).items())),
    }
    if context is not None:
        report["context"] = context
    return report


def evaluate_evidence_file(path: str, control_selector: Optional[str] = None) -> Dict[str, Any]:
    return evaluate_evidence(read_evidence_bytes(path), control_selector)
