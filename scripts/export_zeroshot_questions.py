#!/usr/bin/env python3
"""Write Eric's zero-shot questions in the layout of Mairui's full QA dataset (11-22), so the
two sets can be split by model and run by either of us.

    python3 scripts/export_zeroshot_questions.py

Reads the balanced set (outputs/zeroshot_sample_balanced_11-18-2026.jsonl, 1,069 questions)
and writes, under results/eric zero-shot questions - 11-23-2026/:
  qa/<dataset>_<type>.jsonl   one question per line
  videos.csv                  every video used: dataset, video id, questions on it
  README.md                   counts, videos and fields
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

# set -> (file stem, type, what it asks, time answer scored?)
TYPES = {
    "mb_vts": ("MultiBypass140_FIND", "FIND", "when a phase takes place in the operation", True),
    "mb_ae": ("MultiBypass140_AE", "AE", "did an adverse event happen in the clip; what, when, how serious (yes/no)", True),
    "mb_next": ("MultiBypass140_NEXT", "NEXT", "which step comes next after the clip", False),
    "ct50": ("CholecT50_FIND", "FIND", "when the surgeon does an action to a target", True),
    "c80": ("Cholec80_FIND", "FIND", "when a tool first comes into view", True),
    "jigsaws": ("JIGSAWS_MISTAKE", "MISTAKE", "was there a mistake during a gesture, and which occasion (yes/no)", False),
    "aix": ("AIxSuture_D1", "D1", "coaching feedback on the whole trial", False),
}
ORDER = list(TYPES)


def clock(s):
    s = int(round(s))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def video_id(r):
    return r.get("video") or r.get("trial_id") or r.get("video_id")


def correct_answer(r, s):
    if s == "jigsaws":
        return "Yes" if r["grounding"]["faults"] else "No"
    if s == "mb_ae":
        return "Yes" if str(r["grounding"]["has_event"]) == "True" else "No"
    return None


def convert(r):
    s, z = r["zeroshot"]["set"], r["zeroshot"]
    qa = (r.get("qa") or [{}])[0]
    start, end = z["start_s"], z["end_s"]
    whole = start == 0 and (end is None or s == "mb_vts")
    out = {
        "unit_id": r["qa_id"],
        "dataset": z["dataset"],
        "template_id": TYPES[s][1],
        "question": qa.get("question") or r.get("question"),
        "video": {
            "id": video_id(r),
            "start_s": start,
            "end_s": end,
            "window": "whole recording" if whole else f"{clock(start)}-{clock(end)}",
            "times_count_from": "start of the recording" if z["unit"] == "recording" else "start of the clip",
            "sunflower_path": z["video_path"],
            "oprime_path": z["video_path"].replace("/data/", "/mnt/sun/", 1),
        },
    }
    ca = correct_answer(r, s)
    if ca:
        out["correct_answer"] = ca
    out["ground_truth"] = {
        "reference_answer": qa.get("answer") or r.get("answer"),
        "recorded": r.get("facts_shown_to_model"),
        "localization": "SCORED" if TYPES[s][3] else "NOT APPLICABLE",
    }
    if s == "aix":
        out["ground_truth"]["median_scores"] = r["source_annotation"]["median_scores"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="outputs/zeroshot_sample_balanced_11-18-2026.jsonl")
    ap.add_argument("--out", default="results/eric zero-shot questions - 11-23-2026")
    args = ap.parse_args()
    out = Path(args.out)
    (out / "qa").mkdir(parents=True, exist_ok=True)

    by = defaultdict(list)
    for line in open(args.sample):
        r = json.loads(line)
        by[r["zeroshot"]["set"]].append(convert(r))

    videos = defaultdict(int)
    paths = {}
    rows = []
    for s in ORDER:
        recs = by[s]
        stem, tid, asks, timed = TYPES[s]
        with open(out / "qa" / f"{stem}.jsonl", "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in recs)
        vids = {r["video"]["id"] for r in recs}
        for r in recs:
            videos[(r["dataset"], r["video"]["id"])] += 1
            paths[(r["dataset"], r["video"]["id"])] = r["video"]["oprime_path"]
        yn = ""
        if any("correct_answer" in r for r in recs):
            y = sum(r["correct_answer"] == "Yes" for r in recs)
            yn = f"{y} yes / {len(recs) - y} no"
        if s == "aix":
            b = defaultdict(int)
            for r in recs:
                o = r["ground_truth"]["median_scores"]["overall_performance"]
                b["low" if o <= 2 else "average" if o == 3 else "high"] += 1
            yn = " / ".join(f"{b[k]} {k}" for k in ("low", "average", "high"))
        rows.append((recs[0]["dataset"], tid, asks, len(recs), len(vids), "yes" if timed else "no", yn, stem))

    with open(out / "videos.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dataset", "video_id", "questions", "oprime_path"])
        for (d, v), n in sorted(videos.items()):
            w.writerow([d, v, n, paths[(d, v)]])

    total = sum(r[3] for r in rows)
    table = "\n".join(f"| {d} | {t} | {a} | {n:,} | {v} | {tm} | {yn} | [jsonl](qa/{st}.jsonl) |"
                      for d, t, a, n, v, tm, yn, st in rows)
    n_vid = defaultdict(set)
    for d, v in videos:
        n_vid[d].add(v)
    vid_line = ", ".join(f"{d} {len(v)}" for d, v in sorted(n_vid.items()))
    (out / "README.md").write_text(f"""# Eric's zero-shot questions, 11-23-2026

The {total:,} questions in Eric's balanced zero-shot set (11-18), in the same layout as
Mairui's full QA dataset (11-22), so we can split the models between us and each run both sets.

## Questions

| Dataset | Type | Asks | Questions | Videos | Time scored | Balance | File |
|---|---|---|---:|---:|---|---|---|
{table}
| | | | **{total:,}** | | | | |

Videos: {len(videos)} in all ({vid_line}). [`videos.csv`](videos.csv) lists each one and how
many questions use it.

## Balance

- **MultiBypass140 AE:** 50 clips with a recorded adverse event, 50 without.
- **JIGSAWS MISTAKE:** every question with a recorded mistake (68), and 68 without.
- **AIxSuture D1:** 23 trials per overall rating band: low (1-2), average (3), high (4-5).
- The FIND and NEXT types have no yes/no answer. Every FIND question's target is in the video.

## Video input

- `video.start_s` / `video.end_s` give the stretch to show, in seconds. `end_s` is null when
  the whole recording is shown.
- `times_count_from`: MultiBypass140 AE and NEXT show a clip, and their answers count time
  from the clip's start. Every other type shows the whole recording, and its answers count
  from the recording's start.
- `oprime_path` is where the video is on oprime (`/mnt/sun/shared/datasets/surgical_skill`), and
  `sunflower_path` the same file on sunflower (`/data/shared/...`). CholecT50 is a folder of
  frames at 1 fps, not a video file.
- The 11-18 runs used each model's default video settings, at most 2 fps and 768 frames.

## Fields

- `unit_id`, `dataset`, `template_id`, `question`, `video` as above.
- `correct_answer`: "Yes" or "No", for the AE and MISTAKE types only.
- `ground_truth.reference_answer`: the answer the judge compares against.
- `ground_truth.recorded`: what the annotation records, as given to the question writer.
- `ground_truth.localization`: whether the answer's time is scored.
- `ground_truth.median_scores`: AIxSuture only, the raters' median scores.

Built by [`scripts/export_zeroshot_questions.py`](../../scripts/export_zeroshot_questions.py).
""")
    print(f"{total} questions, {len(videos)} videos -> {out}")
    for r in rows:
        print(f"  {r[0]:15s} {r[1]:8s} {r[3]:4d} questions {r[4]:4d} videos  {r[6]}")


if __name__ == "__main__":
    main()
