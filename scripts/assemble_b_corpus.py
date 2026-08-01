#!/usr/bin/env python3
"""Assemble B-template record files, with later inputs replacing matching selections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ORDER = {f"B{index}": index for index in range(1, 8)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    by_selection: dict[str, dict] = {}
    for path in args.records:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            key = record.get("selection_id") or record["qa_id"]
            by_selection[key] = record

    records = sorted(
        by_selection.values(),
        key=lambda row: (
            ORDER.get(row.get("template_id"), 99),
            row.get("dataset", ""),
            row.get("video_id") or row.get("trial_id") or "",
            row.get("selection_id", ""),
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"assembled {len(records)} records -> {args.output}")


if __name__ == "__main__":
    main()
