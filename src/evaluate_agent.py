"""Run the natural-language-to-SQL benchmark and write a JSON score report."""

import argparse
import json
import math
import time
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path

import duckdb
import pandas as pd

from src.ai_agent import DATABASE_PATH, generate_and_execute_sql, validate_and_limit_sql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = PROJECT_ROOT / "eval" / "golden_questions.json"
DEFAULT_REPORT = PROJECT_ROOT / "eval" / "reports" / "latest.json"


def _normalize_value(value):
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    return value


def _canonical_result(frame: pd.DataFrame):
    """Compare values without relying on row ordering or generated column aliases."""
    rows = [tuple(_normalize_value(value) for value in row) for row in frame.itertuples(index=False, name=None)]
    return sorted(rows, key=repr)


def _expected_result(expected_sql: str) -> pd.DataFrame:
    safe_expected_sql = validate_and_limit_sql(expected_sql)
    with duckdb.connect(DATABASE_PATH, read_only=True) as conn:
        return conn.execute(safe_expected_sql).df()


def evaluate(questions_path: Path, report_path: Path, request_interval_seconds: float = 5.0):
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if not questions:
        raise ValueError("The benchmark has no questions.")
    ids = [item["id"] for item in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Benchmark question IDs must be unique.")

    expected = {item["id"]: _expected_result(item["expected_sql"]) for item in questions}
    records = []
    for index, item in enumerate(questions):
        if index and request_interval_seconds > 0:
            time.sleep(request_interval_seconds)
        started = time.perf_counter()
        generated_sql, result, metadata = generate_and_execute_sql(
            item["question"], include_metadata=True
        )
        quota_retries = 0
        while isinstance(result, str) and "429 RESOURCE_EXHAUSTED" in result and quota_retries < 2:
            quota_retries += 1
            time.sleep(25)
            generated_sql, result, metadata = generate_and_execute_sql(
                item["question"], include_metadata=True
            )
        elapsed = time.perf_counter() - started
        passed_validation = bool(generated_sql)
        executed = passed_validation and not isinstance(result, str)
        matched = False
        if executed:
            matched = _canonical_result(result) == _canonical_result(expected[item["id"]])

        records.append({
            "id": item["id"],
            "question": item["question"],
            "validation_passed": passed_validation,
            "execution_succeeded": executed,
            "result_matched": matched,
            "latency_seconds": round(elapsed, 3),
            "quota_retries": quota_retries,
            "repair_attempts": metadata["repair_attempts"],
            "repair_succeeded": metadata["repair_succeeded"],
            "generated_sql": generated_sql or None,
            "error": result if isinstance(result, str) else None,
        })
        state = "match" if matched else ("executed, result differs" if executed else "failed")
        print(f"{item['id']} {state} ({elapsed:.1f}s)")

    total = len(records)
    validation_count = sum(record["validation_passed"] for record in records)
    execution_count = sum(record["execution_succeeded"] for record in records)
    match_count = sum(record["result_matched"] for record in records)
    repaired_count = sum(record["repair_succeeded"] for record in records)
    repair_attempt_count = sum(record["repair_attempts"] for record in records)
    request_failures = sum(
        isinstance(record["error"], str) and record["error"].startswith("Could not generate SQL:")
        for record in records
    )
    model_responses = total - request_failures
    has_scores = model_responses > 0
    report = {
        "status": "complete" if has_scores else "blocked_no_model_responses",
        "benchmark": str(questions_path.relative_to(PROJECT_ROOT)),
        "model": "gemini-3.5-flash-lite",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "comparison": "Result values compared as unordered rows; aliases ignored; floats rounded to 6 decimal places.",
        "request_interval_seconds": request_interval_seconds,
        "scores": {
            "total_questions": total,
            "model_responses": model_responses,
            "validation_pass_rate": round(validation_count / total, 4) if has_scores else None,
            "execution_accuracy": round(execution_count / total, 4) if has_scores else None,
            "result_match_rate": round(match_count / total, 4) if has_scores else None,
            "result_match_given_execution": round(match_count / execution_count, 4) if execution_count else None,
            "model_request_failures": request_failures,
            "queries_repaired": repaired_count,
            "sql_repair_attempts": repair_attempt_count,
            "mean_latency_seconds": round(sum(r["latency_seconds"] for r in records) / total, 3),
        },
        "status_detail": None if has_scores else "No Gemini model responses were received; DNS/network access failed.",
        "results": records,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["scores"], indent=2))
    print(f"Report saved to {report_path}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--interval-seconds", type=float, default=5.0,
                        help="Pause between model requests to stay within API rate limits (default: 5).")
    args = parser.parse_args()
    evaluate(args.questions.resolve(), args.output.resolve(), args.interval_seconds)


if __name__ == "__main__":
    main()
