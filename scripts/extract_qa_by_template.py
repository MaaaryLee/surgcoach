#!/usr/bin/env python3
"""Create one readable Markdown file per QA template from provenance JSONL."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", help="Input qa_records.jsonl files")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for filename in args.records:
        path = Path(filename)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("validation_status") == "valid" and record.get("qa"):
                grouped[record["template_id"]].append(record)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for template_id, records in sorted(grouped.items()):
        records.sort(
            key=lambda r: (
                r["procedure_or_task"],
                r.get("trial_id") or r.get("video_id") or r.get("selection_id"),
            )
        )
        lines = [
            f"# Template {template_id} QA Pairs",
            "",
            f"{len(records)} valid QA pairs across "
            f"{len({r['procedure_or_task'] for r in records})} surgery tasks.",
            "",
        ]
        for record in records:
            qa = record["qa"][0]
            item_id = (
                record.get("trial_id")
                or record.get("video_id")
                or record.get("selection_id")
            )
            lines += [
                (
                    f"## {record['dataset']} — {record['procedure_or_task']} "
                    f"— {item_id}"
                ),
                "",
                f"**Timestamp:** {record['timestamp_or_frame']}",
                "",
                f"**Q:** {qa['question']}",
                "",
                f"**A:** {qa['answer']}",
                "",
                f"**Rationale:** {qa['rationale']}",
                "",
            ]
        destination = output_dir / f"{template_id}.md"
        destination.write_text("\n".join(lines), encoding="utf-8")
        print(f"{template_id}: {len(records)} pairs -> {destination}")


if __name__ == "__main__":
    main()
