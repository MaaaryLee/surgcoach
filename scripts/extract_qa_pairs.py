#!/usr/bin/env python3
"""Extract clean QA pairs from full-provenance qa_records.jsonl files.

Reads a batch's qa_records.jsonl (one full record per line, as written by
run_annotation_qa_jigsaws.py), keeps only valid records, and writes next to it:

- qa_pairs.jsonl : one minimal object per line
                   {qa_id, template_id, trial_id, frames, gesture, question, answer, rationale}
- qa_pairs.md    : human-readable Markdown grouped by template

Provenance stays in qa_records.jsonl; these files are views, not the source of truth.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TEMPLATE_NAMES = {
    "A3": "Action Recognition",
    "C1": "Tissue Handling Score",
    "C2": "Instrument Handling Score",
    "C3": "Economy of Motion",
    "C6": "Flow of Operation",
    "C7": "Autonomy Level",
    "D1": "Coaching Feedback",
    "D2": "Feedback With Priority",
    "D3": "Corrective Action",
    "D4": "Practice Recommendation",
    "D5": "Positive Reinforcement",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", help="Path(s) to qa_records.jsonl")
    args = parser.parse_args()

    for records_path in args.records:
        path = Path(records_path)
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        valid = [r for r in records if r.get("validation_status") == "valid" and r.get("qa")]

        pairs = []
        for r in valid:
            qa = r["qa"][0]
            pairs.append(
                {
                    "qa_id": r["qa_id"],
                    "template_id": r["template_id"],
                    "trial_id": r["trial_id"],
                    "frames": r["timestamp_or_frame"],
                    # Video-granularity records cover a whole trial and have no
                    # single gesture; they carry a timestamp span and a gesture
                    # count instead. Keep the key so both granularities produce
                    # the same shape, and report the span rather than crashing.
                    "gesture": r["source_annotation"].get("gesture_id")
                    or f"whole video ({r['source_annotation'].get('gesture_count', '?')} gestures)",
                    "question": qa["question"],
                    "answer": qa["answer"],
                    "rationale": qa["rationale"],
                }
            )

        jsonl_path = path.parent / "qa_pairs.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for pair in pairs:
                handle.write(json.dumps(pair, ensure_ascii=False) + "\n")

        md_lines = [
            f"# QA Pairs: {path.parent.name}",
            "",
            f"{len(pairs)} valid pairs extracted from `qa_records.jsonl` "
            f"(trial {pairs[0]['trial_id']})." if pairs else "No valid pairs.",
            "",
        ]
        for template_id in sorted({p["template_id"] for p in pairs}):
            title = TEMPLATE_NAMES.get(template_id, template_id)
            md_lines += [f"## {template_id}: {title}", ""]
            for p in [x for x in pairs if x["template_id"] == template_id]:
                md_lines += [
                    f"### Frames {p['frames']} (gesture {p['gesture']})",
                    "",
                    f"**Q:** {p['question']}",
                    "",
                    f"**A:** {p['answer']}",
                    "",
                    f"**Rationale:** {p['rationale']}",
                    "",
                ]
        md_path = path.parent / "qa_pairs.md"
        md_path.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"{path.parent.name}: {len(pairs)} pairs -> {jsonl_path.name}, {md_path.name}")


if __name__ == "__main__":
    main()
