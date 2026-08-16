"""Evaluation runner.

Loads eval/eval_cases.json, runs each case against the real application stack
(orchestrator + mock provider, so it is deterministic and needs no API key),
applies the machine-checkable judgment for each case, and prints a report.

Usage:
    python -m eval.run_eval            # human-readable report
    python -m eval.run_eval --json     # machine-readable results
    python -m eval.run_eval --report eval/report.md   # write a Markdown report

Exit code is non-zero if any case fails, so this can gate CI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# Force deterministic, offline execution BEFORE importing the app.
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ["DB_PATH"] = str(Path(tempfile.gettempdir()) / "wavy_eval.db")

from app import db  # noqa: E402
from app.models import TutorResponse  # noqa: E402
from app.retrieval import load_curriculum  # noqa: E402
from app.tutor import handle_message  # noqa: E402

CASES_PATH = Path(__file__).parent / "eval_cases.json"
ALL_CURRICULUM_IDS = {i.id for i in load_curriculum()}


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Execute one case and return the last turn's response + telemetry +
    retrieval ids."""
    db.init_db()
    session = db.create_session("eval", "en")
    sid = session["id"]

    inp = case["input"]
    turns = inp["turns"] if "turns" in inp else [inp["message"]]
    result = None
    for message in turns:
        result = handle_message(sid, message)
    return result


def _judge(case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, list[str]]:
    """Apply the case's expectations. Returns (passed, failure_reasons)."""
    expect = case.get("expect", {})
    resp = result["response"]
    telem = result["telemetry"]
    retrieval_ids = telem.get("retrieval_ids", [])
    reply_text = (resp.get("tutorMessage", "") + " " + (resp.get("nextQuestion") or ""))
    reasons: list[str] = []

    if expect.get("schema_valid"):
        try:
            TutorResponse.model_validate(resp)
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"schema_valid: {exc}")

    if "retrieval_includes_prefix" in expect:
        pref = expect["retrieval_includes_prefix"]
        if not any(i.startswith(pref) for i in retrieval_ids):
            reasons.append(f"retrieval_includes_prefix {pref!r}: got {retrieval_ids}")

    if "retrieval_includes_any" in expect:
        wanted = set(expect["retrieval_includes_any"])
        if not wanted & set(retrieval_ids):
            reasons.append(f"retrieval_includes_any {wanted}: got {retrieval_ids}")

    if expect.get("retrieval_empty"):
        if retrieval_ids:
            reasons.append(f"retrieval_empty: got {retrieval_ids}")

    if expect.get("citations_subset_of_retrieved"):
        cites = set(resp.get("curriculumCitations", []))
        if not cites <= set(retrieval_ids):
            reasons.append(f"citations not subset of retrieved: {cites} vs {retrieval_ids}")

    if expect.get("citations_are_real_ids"):
        cites = set(resp.get("curriculumCitations", []))
        if not cites <= ALL_CURRICULUM_IDS:
            reasons.append(f"citations contain unknown ids: {cites - ALL_CURRICULUM_IDS}")

    if expect.get("has_next_question"):
        if not resp.get("nextQuestion"):
            reasons.append("has_next_question: nextQuestion was empty")

    if "final_reply_references_any" in expect:
        wanted = expect["final_reply_references_any"]
        if not any(w in reply_text for w in wanted):
            reasons.append(f"final_reply_references_any {wanted}: not found in reply")

    if "safety_flags_include" in expect:
        flag = expect["safety_flags_include"]
        if flag not in resp.get("safetyFlags", []):
            reasons.append(f"safety_flags_include {flag!r}: got {resp.get('safetyFlags')}")

    if "reply_excludes_all" in expect:
        for banned in expect["reply_excludes_all"]:
            if banned in reply_text:
                reasons.append(f"reply leaked banned text: {banned!r}")

    if "reply_mentions" in expect:
        if expect["reply_mentions"].lower() not in reply_text.lower():
            reasons.append(f"reply_mentions {expect['reply_mentions']!r}: not found")

    if expect.get("confidence_in_range"):
        c = resp.get("confidence")
        if not (isinstance(c, (int, float)) and 0.0 <= c <= 1.0):
            reasons.append(f"confidence_in_range: got {c}")

    if "telemetry_has_fields" in expect:
        for field in expect["telemetry_has_fields"]:
            if field not in telem or telem[field] is None:
                reasons.append(f"telemetry missing field: {field}")

    return (not reasons, reasons)


def run(write_report: str | None = None) -> tuple[int, int, list[dict]]:
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    results = []
    passed = 0
    for case in cases:
        try:
            outcome = _run_case(case)
            ok, reasons = _judge(case, outcome)
        except Exception as exc:  # noqa: BLE001
            ok, reasons = False, [f"exception: {type(exc).__name__}: {exc}"]
        passed += int(ok)
        results.append({"id": case["id"], "passed": ok, "reasons": reasons,
                        "description": case["description"]})
    if write_report:
        _write_markdown(write_report, results, passed, len(cases))
    return passed, len(cases), results


def _write_markdown(path: str, results: list[dict], passed: int, total: int) -> None:
    lines = [f"# Wavy Tutor — Evaluation Report", "",
             f"**{passed}/{total} cases passed**", "",
             "| Case | Result | Notes |", "|---|---|---|"]
    for r in results:
        status = "✅ pass" if r["passed"] else "❌ fail"
        notes = "" if r["passed"] else "; ".join(r["reasons"])
        lines.append(f"| `{r['id']}` | {status} | {notes} |")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--report", help="write a Markdown report to this path")
    args = parser.parse_args()

    passed, total, results = run(write_report=args.report)

    if args.json:
        print(json.dumps({"passed": passed, "total": total, "results": results}, indent=2))
    else:
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"[{status}] {r['id']} — {r['description']}")
            for reason in r["reasons"]:
                print(f"         ↳ {reason}")
        print(f"\n{passed}/{total} eval cases passed.")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
