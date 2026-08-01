#!/usr/bin/env python3
"""Audit B1/B3/B4/B5 annotation-first generation records."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from run_annotation_qa_b1345 import (
    ANSWER_META_RE,
    RAW_DECIMAL_RE,
    VISUAL_MARKER,
    expected_question,
    load_template_questions,
)
from run_annotation_qa_jigsaws import thinking_trace_returned


EXPECTED_COUNTS = {"B1": 9, "B3": 9, "B4": 6, "B5": 9}
EXPECTED_DATASETS = {
    "B1": {"BernBypass70": 3, "StrasBypass70": 3, "Endoscapes2023": 3},
    "B3": {"BernBypass70": 3, "StrasBypass70": 3, "Endoscapes2023": 3},
    "B4": {"BernBypass70": 3, "StrasBypass70": 3},
    "B5": {"BernBypass70": 3, "StrasBypass70": 3, "Endoscapes2023": 3},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--template-questions", required=True)
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    manifest_by_id = {item["selection_id"]: item for item in manifest}
    questions = load_template_questions(Path(args.template_questions))
    records = []
    for filename in args.records:
        records.extend(
            json.loads(line)
            for line in Path(filename).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    errors: list[str] = []
    counts = Counter(record.get("template_id") for record in records)
    dataset_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        dataset_counts[record.get("template_id")][record.get("dataset")] += 1

    if dict(counts) != EXPECTED_COUNTS:
        errors.append(f"template counts: {dict(counts)} != {EXPECTED_COUNTS}")
    for template_id, expected in EXPECTED_DATASETS.items():
        if dict(dataset_counts[template_id]) != expected:
            errors.append(
                f"{template_id} dataset counts: "
                f"{dict(dataset_counts[template_id])} != {expected}"
            )

    seen_ids: set[str] = set()
    for record in records:
        selection_id = record.get("selection_id")
        item = manifest_by_id.get(selection_id)
        prefix = record.get("qa_id") or selection_id or "unknown"
        if item is None:
            errors.append(f"{prefix}: selection absent from manifest")
            continue
        if selection_id in seen_ids:
            errors.append(f"{prefix}: duplicate selection")
        seen_ids.add(selection_id)
        if record.get("validation_status") != "valid":
            errors.append(f"{prefix}: status {record.get('validation_status')}")
        qa_items = record.get("qa") or []
        if len(qa_items) != 1:
            errors.append(f"{prefix}: expected one QA object")
            continue
        qa = qa_items[0]
        expected = expected_question(
            item,
            record["timestamp_or_frame"],
            questions,
        )
        if qa.get("question") != expected:
            errors.append(f"{prefix}: question mismatch")
        answer = qa.get("answer", "")
        rationale = qa.get("rationale", "")
        if ANSWER_META_RE.search(answer):
            errors.append(
                f"{prefix}: answer meta leak "
                f"{ANSWER_META_RE.search(answer).group(0)!r}"
            )
        if RAW_DECIMAL_RE.search(answer):
            errors.append(
                f"{prefix}: answer numeric leak "
                f"{RAW_DECIMAL_RE.search(answer).group(0)!r}"
            )
        raw_output = record.get("model", {}).get("raw_output", "")
        thinking = record.get("model", {}).get("thinking", {})
        if not (
            thinking.get("enabled")
            and thinking.get("required")
            and thinking.get("trace_returned")
            and thinking_trace_returned(raw_output)
        ):
            errors.append(f"{prefix}: thinking not verified")
        if not re.search(
            r"\b\d{1,3}:\d{2}(?:-\d{1,3}:\d{2})?\b",
            record.get("timestamp_or_frame", ""),
        ):
            errors.append(f"{prefix}: missing timestamp-form reference")
        if record["template_id"] == "B4":
            if not re.match(r"^\s*No\.", answer):
                errors.append(f"{prefix}: B4 must begin No.")
            if not (
                rationale.count(VISUAL_MARKER) == 1
                and rationale.rstrip().endswith(VISUAL_MARKER)
                and VISUAL_MARKER not in answer
            ):
                errors.append(f"{prefix}: B4 marker format")
        if record["template_id"] == "B5":
            if record["timestamp_or_frame"] not in answer:
                errors.append(f"{prefix}: B5 timestamp absent from answer")
            if not (
                rationale.count(VISUAL_MARKER) == 1
                and rationale.rstrip().endswith(VISUAL_MARKER)
                and VISUAL_MARKER not in answer
            ):
                errors.append(f"{prefix}: B5 marker format")

    if set(manifest_by_id) != seen_ids:
        missing = sorted(set(manifest_by_id) - seen_ids)
        errors.append(f"missing manifest selections: {missing}")

    for template_id, expected in EXPECTED_DATASETS.items():
        for dataset, expected_count in expected.items():
            videos = {
                record["video_id"]
                for record in records
                if record["template_id"] == template_id
                and record["dataset"] == dataset
            }
            if len(videos) != expected_count:
                errors.append(
                    f"{template_id}/{dataset}: {len(videos)} unique videos, "
                    f"expected {expected_count}"
                )

    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    print("PASS")
    print(f"records={len(records)}; valid={len(records)}; rejected=0")
    print(
        "templates="
        + ",".join(f"{key}:{counts[key]}" for key in sorted(EXPECTED_COUNTS))
    )
    print(f"thinking_verified={len(records)}")
    print("exact_questions=33; timestamp_form=33; answer_meta_leaks=0")
    print("B4_negative_controls=6; B4_positive_examples=0")
    print(
        "dataset_distribution="
        + ";".join(
            f"{template}:"
            + ",".join(
                f"{dataset}={count}"
                for dataset, count in sorted(dataset_counts[template].items())
            )
            for template in sorted(dataset_counts)
        )
    )


if __name__ == "__main__":
    main()
