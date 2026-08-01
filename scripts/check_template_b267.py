#!/usr/bin/env python3
"""Independent audit for the selected thinking-enabled B2/B6/B7 batch."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_annotation_qa_b267 import (  # noqa: E402
    ANSWER_META_RE,
    RAW_DECIMAL_RE,
    VISUAL_MARKER,
    expected_question,
    load_template_questions,
)
from run_annotation_qa_jigsaws import thinking_trace_returned  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--template-questions", required=True)
    args = parser.parse_args()

    records = []
    for filename in args.records:
        records.extend(
            json.loads(line)
            for line in Path(filename).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    selections = {item["selection_id"]: item for item in manifest}
    questions = load_template_questions(Path(args.template_questions))
    issues: list[str] = []

    def issue(record: dict, message: str) -> None:
        issues.append(f"{record.get('qa_id', '<missing qa_id>')}: {message}")

    if len(records) != 20:
        issues.append(f"batch count is {len(records)}, expected 20")
    qa_ids = [record.get("qa_id") for record in records]
    if len(set(qa_ids)) != len(qa_ids):
        issues.append("qa_id values are not unique")
    template_counts = Counter(record.get("template_id") for record in records)
    if template_counts != Counter({"B2": 10, "B6": 5, "B7": 5}):
        issues.append(f"wrong template counts: {dict(template_counts)}")
    dataset_template_counts = Counter(
        (record.get("dataset"), record.get("template_id")) for record in records
    )
    expected_dataset_template = Counter(
        {
            ("CholecT50", "B2"): 5,
            ("Endoscapes2023", "B2"): 5,
            ("MultiBypass140", "B6"): 5,
            ("Endoscapes2023", "B7"): 5,
        }
    )
    if dataset_template_counts != expected_dataset_template:
        issues.append(
            f"wrong dataset/template counts: {dict(dataset_template_counts)}"
        )

    b6_categories = set()
    b7_profiles = set()
    cholec_triplets = set()
    endoscapes_b2_profiles = set()

    for record in records:
        selection_id = record.get("selection_id")
        item = selections.get(selection_id)
        if item is None:
            issue(record, f"selection_id {selection_id!r} is absent from manifest")
            continue
        if record.get("validation_status") != "valid":
            issue(record, f"record is not valid: {record.get('validation_error')}")
            continue
        if not re.fullmatch(
            r"\d+:\d{2}(?:-\d+:\d{2})?", record.get("timestamp_or_frame", "")
        ):
            issue(record, "timestamp_or_frame is not a timestamp")
        model = record.get("model", {})
        thinking = model.get("thinking", {})
        if not (
            thinking.get("enabled")
            and thinking.get("required")
            and thinking.get("trace_returned")
            and thinking_trace_returned(model.get("raw_output", ""))
        ):
            issue(record, "thinking trace is not fully verified")
        qa_items = record.get("qa") or []
        if len(qa_items) != 1:
            issue(record, f"expected one QA object, found {len(qa_items)}")
            continue
        qa = qa_items[0]
        expected = expected_question(
            item, record["timestamp_or_frame"], questions
        )
        if qa["question"] != expected:
            issue(record, "question does not match the canonical template")
        answer = qa["answer"]
        rationale = qa["rationale"]
        meta_match = ANSWER_META_RE.search(answer)
        if meta_match:
            issue(record, f"Rule 11 leak in answer: {meta_match.group(0)!r}")
        numeric_match = RAW_DECIMAL_RE.search(answer)
        if numeric_match:
            issue(record, f"Rule 11 numeric leak in answer: {numeric_match.group(0)!r}")

        template_id = record["template_id"]
        if template_id == "B2":
            if not answer.startswith(
                "No—do not proceed without direct visual confirmation."
            ):
                issue(record, "B2 conservative opening is absent")
            if not (
                answer.count(VISUAL_MARKER) == 1
                and rationale.count(VISUAL_MARKER) == 1
                and answer.rstrip().endswith(VISUAL_MARKER)
                and rationale.rstrip().endswith(VISUAL_MARKER)
            ):
                issue(record, "B2 visual-evidence marker format is incorrect")
        elif template_id == "B6":
            if record["timestamp_or_frame"] not in answer:
                issue(record, "B6 answer omits the event timestamp")
            b6_categories.add(record["source_annotation"]["iae_category"])
        elif template_id == "B7":
            if not re.match(
                r"^\s*(yes|no|not yet)\b", answer, flags=re.IGNORECASE
            ):
                issue(record, "B7 answer does not begin with a safety decision")
            source = record["source_annotation"]
            b7_profiles.add((source["C1"], source["C2"], source["C3"]))

        if record["dataset"] == "CholecT50":
            cholec_triplets.add(tuple(record["source_annotation"]["action_triplets"]))
        if record["dataset"] == "Endoscapes2023" and template_id == "B2":
            source = record["source_annotation"]
            endoscapes_b2_profiles.add(
                (source["C1"], source["C2"], source["C3"])
            )

    if len(b6_categories) != 5:
        issues.append(f"B6 has {len(b6_categories)} event categories, expected 5")
    if len(b7_profiles) != 5:
        issues.append(f"B7 has {len(b7_profiles)} CVS profiles, expected 5")
    if len(cholec_triplets) != 5:
        issues.append(
            f"CholecT50 B2 has {len(cholec_triplets)} triplet sets, expected 5"
        )
    if len(endoscapes_b2_profiles) != 5:
        issues.append(
            "Endoscapes B2 has "
            f"{len(endoscapes_b2_profiles)} CVS profiles, expected 5"
        )

    if issues:
        print(f"FAIL: {len(issues)} issue(s)")
        for message in issues:
            print(f"- {message}")
        return 1
    print("PASS")
    print("records=20; valid=20; rejected=0")
    print("templates=B2:10,B6:5,B7:5")
    print("thinking_verified=20")
    print("exact_questions=20; timestamps=20; rule11_answer_leaks=0")
    print(
        "annotation_diversity="
        "B2_CholecT50_triplets:5,"
        "B2_Endoscapes_CVS_profiles:5,"
        "B6_event_categories:5,"
        "B7_CVS_profiles:5"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
