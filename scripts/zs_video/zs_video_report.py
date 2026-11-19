#!/usr/bin/env python3
"""One results table for the zero-shot video run: models x question types.

python3 -I scripts/zs_video/zs_video_report.py <run dir> <out dir> [--judge-id gemma-3-27b-it_Q4_K_M]

Reads <run dir>/video_units/*.jsonl and <run dir>/outputs/<model>/{generations,reviews-<judge>}.jsonl.
Writes <out dir>/summary.csv, summary.json, results.md (the table to send) and
records/<model>/<dataset>_<type>.jsonl (question, answer, scores, judge note; no frames).

Columns per model and type: questions, answered, judged, and the judge's mean label,
localization and scope scores (1-5). Localization counts only where the question asks when and
the annotation records a time (an answer with no time scores 1). Where it records one span, the
table instead checks the rubric's localization 5 without the judge: the answer's first time falls
in or overlaps that span, give or take one frame spacing. Two more checks need no judge. For the JIGSAWS VTS locate
questions: the time overlap of the answer's first span with the recorded span (mean IoU and the
share with IoU >= 0.3). For Endoscapes B7 (safe to clip or cut?): whether the answer's opening
yes/no matches the recorded CVS state, plainly and balanced over the two verdicts.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

C = ("label_score", "localization_score", "scope_score")
ORDER = ["JIGSAWS", "MultiBypass140", "Endoscapes2023"]
TYPE_NAMES = {
    ("JIGSAWS", "VTS"): "VTS locate", ("JIGSAWS", "D1"): "D1 overall feedback",
    ("JIGSAWS", "D2"): "D2 improve first", ("JIGSAWS", "D5"): "D5 went best",
    ("MultiBypass140", "D1"): "D1 overall feedback", ("MultiBypass140", "D2"): "D2 address first",
    ("MultiBypass140", "D5"): "D5 went best", ("MultiBypass140", "B1"): "B1 urgent concern",
    ("MultiBypass140", "B3"): "B3 rank risks", ("MultiBypass140", "B4"): "B4 near miss?",
    ("MultiBypass140", "B5"): "B5 when to pause (safety)",
    ("MultiBypass140", "B6"): "B6 when to pause (coaching)",
    ("Endoscapes2023", "B1"): "B1 urgent concern", ("Endoscapes2023", "B2"): "B2 safe to proceed?",
    ("Endoscapes2023", "B3"): "B3 rank risks", ("Endoscapes2023", "B5"): "B5 when to pause",
    ("Endoscapes2023", "B7"): "B7 safe to clip?",
}
TIME = r"\d+:\d{2}(?::\d{2})?(?:\.\d+)?"


def secs(label: str) -> float:
    s = 0.0
    for p in label.split(":"):
        s = s * 60 + float(p)
    return s


def first_span(answer: str) -> tuple[float, float] | None:
    """The answer's first time: a span when a range follows it ("0:28 to 0:35", "0:28–0:35",
    "between 0:28 and 0:35"), else one moment. A later time ("and again at 0:48") is not part of it."""
    m = re.search(TIME, answer)
    if not m:
        return None
    joins = "-|–|—|to|until|through"
    if re.search(r"\bbetween\b[\s*a-z]{0,20}$", answer[:m.start()].lower()):
        joins += "|and"
    n = re.match(rf"\s*\**\s*(?:{joins})\s*\**\s*({TIME})", answer[m.end():])
    a = secs(m.group(0))
    b = secs(n.group(1)) if n else a
    return (min(a, b), max(a, b))


def recorded_span(u: dict) -> tuple[float, float] | None:
    """The one span the annotation records for a question that asks when (VTS: the answer span;
    MB140 B5/B6: the event's timestamp). None when it records several spans (MB140 D2) or none."""
    span = (u.get("text_only_meta") or {}).get("answer_span")
    if span:
        return (span["start_s"], span["end_s"])
    rec = u["ground_truth"].get("recorded", "")
    text = "\n".join(map(str, rec)) if isinstance(rec, list) else str(rec)
    found = {(secs(a), secs(b)) for a, b in re.findall(rf"({TIME})\s*(?:-|–|—|to)\s*({TIME})", text)}
    return found.pop() if len(found) == 1 else None


def frame_spacing(u: dict) -> float:
    ts = [f["t"] for f in u["frames"]]
    return st.median(b - a for a, b in zip(ts, ts[1:])) if len(ts) > 1 else 1.0


def expected_b7(gt: dict) -> str | None:
    """Endoscapes B7 asks whether it is safe to clip or cut. The recorded answer: safe only when
    all three CVS criteria are achieved by a majority of the three annotators (mean >= 0.5)."""
    rec = gt.get("recorded")
    lines = rec if isinstance(rec, list) else str(rec).splitlines()
    vals = [float(m.group(1)) for line in lines for m in [re.match(r"\s*cvs_\w+:\s*([0-9.]+)", str(line))] if m]
    if len(vals) != 3:
        return None
    return "safe" if all(v >= 0.5 for v in vals) else "not safe"


def answer_verdict(answer: str) -> str | None:
    """The yes/no an answer opens with, read from its first sentence."""
    first = re.split(r"(?<=[.!?])\s", answer.strip(), maxsplit=1)[0].lower()
    if re.match(r"\W*(no|not yet)\b", first) or re.search(
            r"\bnot (yet )?(medically )?safe\b|\bunsafe\b|\bshould not\b|\bnot advisable\b|"
            r"\bcannot proceed\b|\bcan't proceed\b|\bpremature\b", first):
        return "not safe"
    if re.match(r"\W*yes\b", first) or re.search(
            r"\bsafe to (proceed|clip|cut)\b|\bit is safe\b|\b(can|may) (safely )?proceed\b", first):
        return "safe"
    return None


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def mean(v):
    return round(st.mean(v), 2) if v else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--judge-id", default=None, help="which reviews-<judge>.jsonl to read (default: the only one)")
    args = ap.parse_args()
    units = {}
    for f in glob.glob(str(args.run / "video_units/*.jsonl")):
        for line in open(f, encoding="utf-8"):
            u = json.loads(line)
            units[u["unit_id"]] = u

    rows, models, untimed = [], [], set()
    # one model per outputs/<model>/; a model split over several jobs has one subfolder per part
    for model_dir in sorted(p for p in (args.run / "outputs").iterdir() if p.is_dir()):
        gen_files = sorted(model_dir.rglob("generations.jsonl"))
        if not gen_files:
            continue
        gens = {}
        for gen_path in gen_files:
            for line in open(gen_path, encoding="utf-8"):
                g = json.loads(line)
                if g.get("answer") or g["unit_id"] not in gens:
                    gens[g["unit_id"]] = g
        model_id = next(iter(gens.values()))["model_id"] if gens else model_dir.name
        revs_files = sorted(model_dir.rglob("reviews-*.jsonl"))
        if args.judge_id:
            revs_files = [p for p in revs_files if p.stem == f"reviews-{args.judge_id}"]
        elif len({p.stem for p in revs_files}) > 1:
            raise SystemExit(f"{model_dir.name} has reviews from several judges "
                             f"({', '.join(sorted({p.stem[8:] for p in revs_files}))}); pass --judge-id")
        revs, judge_id = {}, None
        for rev_path in revs_files:
            for line in open(rev_path, encoding="utf-8"):
                r = json.loads(line)
                if r.get("judged") or r["unit_id"] not in revs:
                    revs[r["unit_id"]] = r
                judge_id = r.get("judge_id") or judge_id
        models.append({"model": model_dir.name, "model_id": model_id, "judge_id": judge_id})
        groups = defaultdict(list)
        for uid, u in units.items():
            g, r = gens.get(uid), revs.get(uid, {})
            rec = {"unit_id": uid, "question": u["question"], "frames_shown": len(u["frames"]),
                   "window": f"{u['frames'][0]['label']}-{u['frames'][-1]['label']}",
                   "answer": g and g.get("answer"), "status": g["status"] if g else "not_run",
                   **{c: r.get(c) for c in C}, "judged": bool(r.get("judged")),
                   "judge_note": r.get("evaluator_notes") or r.get("reason")}
            loc_asked = not str(u["ground_truth"].get("localization", "")).upper().startswith("NOT")
            # A time can only be scored against a recorded one. Endoscapes B5 asks when to pause,
            # but its annotation holds only the CVS state at one keyframe, with no time.
            loc_applies = loc_asked and bool(re.search(TIME, json.dumps(u["ground_truth"].get("recorded", ""))))
            if loc_asked and not loc_applies:
                untimed.add((u["dataset"], u["template_id"]))
            if g and not rec["answer"]:
                # No answer (e.g. thinking ran past the token limit on all three tries) counts as
                # the lowest score for what and when; scope has nothing to grade.
                rec.update(no_answer=True, label_score=1, scope_score=None,
                           localization_score=1 if loc_applies else None)
            elif not loc_applies:
                # the judge sometimes scores localization where the rubric says it does not apply,
                # or where there is no recorded time to compare with
                rec["localization_score"] = None
            elif rec["judged"] and not re.search(TIME, rec["answer"]):
                # The rubric: where the question asks when, an answer that gives no time scores 1.
                # The judge gave most of these 5 (e.g. "There is no visible event..." on B5).
                rec["localization_judge"] = rec["localization_score"]
                rec["localization_score"] = 1
            span = recorded_span(u) if loc_applies else None
            if span and g:
                # The rubric's localization 5, checked without the judge: the answer's first time
                # or span falls in or overlaps the recorded span, give or take one frame spacing.
                # No answer, or no time in it, is a miss.
                got = first_span(rec["answer"]) if rec["answer"] else None
                tol = frame_spacing(u)
                rec["time_hit"] = bool(got) and got[0] <= span[1] + tol and got[1] >= span[0] - tol
                if u["template_id"] == "VTS":
                    rec["iou"] = round(iou(got, span), 3) if got else 0.0
            if (u["dataset"], u["template_id"]) == ("Endoscapes2023", "B7") and g:
                rec["verdict_expected"] = expected_b7(u["ground_truth"])
                rec["verdict_answer"] = answer_verdict(rec["answer"]) if rec["answer"] else None
            groups[(u["dataset"], u["template_id"])].append(rec)
        rec_dir = args.out / "records" / model_dir.name
        rec_dir.mkdir(parents=True, exist_ok=True)
        for (ds, tid), rs in sorted(groups.items(), key=lambda kv: (ORDER.index(kv[0][0]), kv[0][1])):
            j = [r for r in rs if r["judged"]]
            scored = [r for r in rs if r["judged"] or r.get("no_answer")]
            row = {"model": model_dir.name, "dataset": ds, "question_type": tid,
                   "name": TYPE_NAMES.get((ds, tid), tid), "questions": len(rs),
                   "answered": sum(bool(r["answer"]) for r in rs), "judged": len(j),
                   "no_answer": sum(bool(r.get("no_answer")) for r in rs)}
            for c in C:
                v = [r[c] for r in scored if r[c] is not None]
                row[c.split("_")[0] + "_mean"] = mean(v)
                row[c.split("_")[0] + "_dist"] = " ".join(f"{s}:{n}" for s, n in sorted(Counter(v).items()))
            row["localization_set_1_no_time"] = sum("localization_judge" in r for r in rs)
            hits = [r["time_hit"] for r in rs if "time_hit" in r]
            row["time_hit_share"] = round(sum(hits) / len(hits), 2) if hits else None
            row["time_hit_count"] = sum(hits)
            row["time_hit_judge_localization_1"] = sum(bool(r.get("time_hit")) and r["localization_score"] == 1 for r in rs)
            ious = [r["iou"] for r in rs if "iou" in r]
            row["vts_miou"] = mean(ious)
            row["vts_r_at_0.3"] = round(sum(x >= 0.3 for x in ious) / len(ious), 2) if ious else None
            # B7: does the answer's yes/no match the recorded CVS state? An answer with no readable
            # yes/no counts as a miss. Balanced accuracy is the mean recall of the two verdicts, so
            # always answering "no" scores 0.5.
            vs = [r for r in rs if r.get("verdict_expected")]
            if vs:
                hit = [r["verdict_answer"] == r["verdict_expected"] for r in vs]
                recalls = [st.mean(h for h, r in zip(hit, vs) if r["verdict_expected"] == c)
                           for c in ("safe", "not safe") if any(r["verdict_expected"] == c for r in vs)]
                row["b7_verdict_acc"] = round(st.mean(hit), 2)
                row["b7_verdict_balanced_acc"] = round(st.mean(recalls), 2)
                row["b7_says_safe"] = sum(r["verdict_answer"] == "safe" for r in vs)
                row["b7_expected_safe"] = sum(r["verdict_expected"] == "safe" for r in vs)
                row["b7_unreadable"] = sum(r["verdict_answer"] is None for r in vs)
            rows.append(row)
            with open(rec_dir / f"{ds}_{tid}.jsonl", "w", encoding="utf-8") as fh:
                for r in rs:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    if not rows:
        print("no outputs/*/generations.jsonl under", args.run)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for r in rows for k in r))  # B7 rows carry extra columns
    with open(args.out / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    (args.out / "summary.json").write_text(json.dumps({"models": models, "rows": rows}, indent=1) + "\n")

    # results.md: one row per question type, one column per model (judge label score, 1-5)
    names = [m["model"] for m in models]
    by = {(r["model"], r["dataset"], r["question_type"]): r for r in rows}
    keys = sorted({(r["dataset"], r["question_type"]) for r in rows},
                  key=lambda k: (ORDER.index(k[0]), k[1].replace("VTS", "Z")))
    lines = ["| Dataset | Question type | n | " + " | ".join(names) + " |",
             "|---|---|---|" + "---|" * len(names)]
    for ds, tid in keys:
        n = max(by[(m, ds, tid)]["questions"] for m in names if (m, ds, tid) in by)
        cells = []
        for m in names:
            r = by.get((m, ds, tid))
            cells.append("–" if not r or r["label_mean"] is None else f"{r['label_mean']:.2f}")
        lines.append(f"| {ds} | {TYPE_NAMES.get((ds, tid), tid)} | {n} | " + " | ".join(cells) + " |")
    overall = []
    for m in names:
        v = [r["label_mean"] for r in rows if r["model"] == m and r["label_mean"] is not None]
        overall.append(f"**{st.mean(v):.2f}**" if v else "–")
    lines.append("| **All** | mean over types | | " + " | ".join(overall) + " |")

    # checks computed from the answers alone, without the judge
    def cell(ds, tid, key, fmt="{:.2f}"):
        out = []
        for m in names:
            v = by.get((m, ds, tid), {}).get(key)
            out.append("–" if v is None else fmt.format(v))
        return " | ".join(out)
    def has(k, key):
        return any(by.get((m, *k), {}).get(key) is not None for m in names)
    hit_types = [k for k in keys if has(k, "time_hit_share")]
    judge_types = [k for k in keys if k not in hit_types and has(k, "localization_mean")]
    n_hit = sum(r.get("time_hit_count") or 0 for r in rows)
    n_hit_1 = sum(r.get("time_hit_judge_localization_1") or 0 for r in rows)
    name = lambda k: f"{k[0]} {TYPE_NAMES.get(k, k[1])}"
    checks = ["", "## When it happened, and the yes/no", "",
              "Time in the recorded span: the share of answers whose first time (or span) falls inside "
              "the span the annotation records, or overlaps it, give or take one frame spacing. That is "
              "the rubric's localization 5, checked without the judge. An answer with no time, or no "
              f"answer, is a miss. The judge's own localization score gave 1 (a different time) to {n_hit_1} "
              f"of the {n_hit} answers that pass this check, so the table uses the check; summary.csv "
              "keeps the judge's score."
              + "".join(f" {name(k)} records several events, so its row is the judge's localization (1-5)."
                        for k in judge_types)
              + "".join(f" {name(k)} asks when, but its annotation records no time, so it has no row."
                        for k in sorted(untimed))
              + " The IoU and B7 rows use no judge either.", "",
              "| Check | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    checks += [f"| {name(k)}: time in the recorded span | " + cell(*k, "time_hit_share") + " |" for k in hit_types]
    checks += [f"| {name(k)}: judge's localization (1-5) | " + cell(*k, "localization_mean") + " |" for k in judge_types]
    checks += ["| JIGSAWS VTS: mean time overlap (IoU) with the recorded span | " + cell("JIGSAWS", "VTS", "vts_miou") + " |",
               "| JIGSAWS VTS: share with overlap >= 0.3 | " + cell("JIGSAWS", "VTS", "vts_r_at_0.3") + " |",
               "| Endoscapes B7 safe to clip?: yes/no matches the recorded CVS state | " + cell("Endoscapes2023", "B7", "b7_verdict_acc") + " |",
               "| Endoscapes B7: same, balanced over safe and not safe (0.5 = always one answer) | " + cell("Endoscapes2023", "B7", "b7_verdict_balanced_acc") + " |",
               "| Endoscapes B7: answers saying safe (recorded safe: "
               + str(next((r["b7_expected_safe"] for r in rows if "b7_expected_safe" in r), "–")) + ") | "
               + cell("Endoscapes2023", "B7", "b7_says_safe", "{}") + " |"]
    judges = sorted({m["judge_id"] for m in models if m["judge_id"]})
    head = ["# Zero-shot video QA", "",
            "Models: " + "; ".join(f"`{m['model']}` = {m['model_id']}" for m in models) + ".",
            f"Judge: {', '.join(judges) or 'not run yet'}, with `Prompts_And_Pipeline/vlm-zero-shot-judge-v1.md`.", "",
            "## Does the answer report what the annotation records?", "",
            "The judge's label score, mean over the questions of each type: 5 = everything recorded "
            "is reported, 3 = part of it, 1 = none of it or a contradiction (a different event, a "
            "recorded event denied, the opposite rating or verdict). A question the model gave no "
            "answer to counts as 1"
            + (": " + "; ".join(f"`{r['model']}` {r['dataset']} {r['question_type']} {r['no_answer']}"
                                for r in rows if r.get("no_answer")) + "." if any(r.get("no_answer") for r in rows) else "."),
            ""]
    (args.out / "results.md").write_text("\n".join(head + lines + checks) + "\n")
    print("\n".join(head + lines + checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
