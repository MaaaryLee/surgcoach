#!/usr/bin/env python3
"""Audit the final combined B1-B7 QA corpus before document export."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from run_annotation_qa_b1345 import ANSWER_META_RE, RAW_DECIMAL_RE
from run_annotation_qa_jigsaws import thinking_trace_returned


EXPECTED = Counter(
    {"B1": 9, "B2": 10, "B3": 9, "B4": 6, "B5": 9, "B6": 5, "B7": 5}
)
TIMESTAMP_RE = re.compile(r"\b\d{1,3}:\d{2}(?:-\d{1,3}:\d{2})?\b")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.records.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    issues: list[str] = []
    counts = Counter(row.get("template_id") for row in rows)
    if counts != EXPECTED:
        issues.append(f"template counts {dict(counts)} != {dict(EXPECTED)}")
    qa_ids = [row.get("qa_id") for row in rows]
    if len(qa_ids) != len(set(qa_ids)):
        issues.append("qa_id values are not unique")

    for row in rows:
        key = row.get("qa_id", "<unknown>")
        if row.get("validation_status") != "valid":
            issues.append(f"{key}: status={row.get('validation_status')}")
        qa = row.get("qa") or []
        if len(qa) != 1:
            issues.append(f"{key}: expected exactly one QA object")
            continue
        answer = qa[0].get("answer", "")
        meta = ANSWER_META_RE.search(answer)
        if meta:
            issues.append(f"{key}: answer meta leak {meta.group(0)!r}")
        raw_value = RAW_DECIMAL_RE.search(answer)
        if raw_value:
            issues.append(f"{key}: answer raw value leak {raw_value.group(0)!r}")
        if not TIMESTAMP_RE.search(row.get("timestamp_or_frame", "")):
            issues.append(f"{key}: timestamp is not in minute:second form")
        model = row.get("model", {})
        thinking = model.get("thinking", {})
        if not (
            thinking.get("enabled")
            and thinking.get("required")
            and thinking.get("trace_returned")
            and thinking_trace_returned(model.get("raw_output", ""))
        ):
            issues.append(f"{key}: thinking trace was not verified")

    if issues:
        print(f"FAIL: {len(issues)} issue(s)")
        for issue in issues:
            print(f"- {issue}")
        raise SystemExit(1)
    print("PASS")
    print("records=53; valid=53; rejected=0")
    print(
        "templates="
        + ",".join(f"{key}:{counts[key]}" for key in sorted(EXPECTED))
    )
    print("thinking_verified=53")
    print("timestamps=53; answer_meta_leaks=0; answer_raw_value_leaks=0")


if __name__ == "__main__":
    main()
