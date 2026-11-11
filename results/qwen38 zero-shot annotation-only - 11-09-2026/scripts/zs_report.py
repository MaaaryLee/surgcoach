#!/usr/bin/env python3
"""Collect the Qwen3.8 zero-shot run into a results folder: summary table, per-record files,
and representative examples for an overview.

python3 -I zs_report.py <run dir> <out dir>
"""
import collections
import csv
import glob
import json
import random
import statistics as st
import sys
from pathlib import Path

run, out = Path(sys.argv[1]), Path(sys.argv[2])
(out / "records").mkdir(parents=True, exist_ok=True)
C = ("label_score", "localization_score", "scope_score")
ORDER = ["JIGSAWS", "MultiBypass140", "Endoscapes2023"]

units = {}
for f in glob.glob(str(run / "units/*.jsonl")):
    for l in open(f):
        u = json.loads(l)
        units[u["unit_id"]] = u
gens, revs = {}, {}
for d in glob.glob(str(run / "outputs/job-*")):
    for l in open(d + "/generations.jsonl"):
        g = json.loads(l)
        gens[g["unit_id"]] = g
    p = Path(d) / "reviews.jsonl"
    if p.exists():
        for l in open(p):
            r = json.loads(l)
            revs[r["unit_id"]] = r

groups = collections.defaultdict(list)
for uid, u in units.items():
    g, r = gens.get(uid), revs.get(uid, {})
    rec = {"unit_id": uid, "dataset": u["dataset"], "template_id": u["template_id"],
           "question": u["question"], "answer": g and g["answer"],
           "status": g["status"] if g else "not_run",
           "label_score": r.get("label_score"), "localization_score": r.get("localization_score"),
           "scope_score": r.get("scope_score"), "uncertain": r.get("uncertain"),
           "reviewer_notes": r.get("evaluator_notes") or r.get("reason"),
           "reviewed": bool(r.get("judged")),
           "annotation_facts": u["ground_truth"]["recorded"]}
    groups[(u["dataset"], u["template_id"])].append(rec)

key = lambda k: (ORDER.index(k[0]), k[1].replace("VTS", "Z"))
rows = []
for k in sorted(groups, key=key):
    rs = groups[k]
    j = [r for r in rs if r["reviewed"]]
    row = {"dataset": k[0], "question_type": k[1], "questions": len(rs),
           "no_usable_answer": sum(r["answer"] is None for r in rs),
           "reviewed": len(j)}
    for c in C:
        v = [r[c] for r in j if r[c] is not None]
        name = c.split("_")[0]
        row[f"{name}_mean"] = round(st.mean(v), 2) if v else None
        row[f"{name}_dist"] = " ".join(f"{s}:{n}" for s, n in sorted(collections.Counter(v).items())) if v else "n/a"
    row["defects_any_le3"] = sum(any((r[c] or 5) <= 3 for c in C) for r in j)
    row["clean_all_5"] = sum(all(r[c] in (5, None) for c in C) for r in j)
    rows.append(row)
    with open(out / "records" / f"{k[0]}_{k[1]}.jsonl", "w") as fh:
        for r in rs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

with open(out / "summary.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
(out / "summary.json").write_text(json.dumps(rows, indent=1) + "\n")

# Representative examples: one clean answer per question type (seeded), plus every defect.
rng = random.Random(20261109)
def fmt(v):
    return "n/a" if v is None else str(v)
def block(r, why):
    facts = r["annotation_facts"]
    facts = "\n".join(facts) if isinstance(facts, list) else facts
    return "\n".join([
        f"#### {r['dataset']} {r['template_id']}: {why}",
        "",
        f"**Question:** {r['question']}",
        "",
        "<details><summary>Annotation facts the model was given</summary>",
        "",
        "```text", facts.strip()[:2500], "```", "</details>",
        "",
        f"**Qwen3.8 answer:** {r['answer'] or '(no usable answer)'}",
        "",
        f"**v5 review:** label {fmt(r['label_score'])} · localization {fmt(r['localization_score'])} · "
        f"scope {fmt(r['scope_score'])}" + (f" — {r['reviewer_notes']}" if r['reviewer_notes'] else ""),
        "",
    ])
clean, defects = [], []
for k in sorted(groups, key=key):
    rs = [r for r in groups[k] if r["reviewed"]]
    ok = [r for r in rs if all(r[c] in (5, None) for c in C)]
    if ok:
        clean.append(block(rng.choice(ok), "typical answer (all 5s)"))
    for r in rs:
        if any((r[c] or 5) <= 3 for c in C):
            defects.append(block(r, "defect (a score of 3 or below)"))
(out / "representative-examples.md").write_text(
    "# Qwen3.8 zero-shot: representative questions\n\n"
    "One typical answer per question type, then every answer the reviewer scored 3 or below.\n\n"
    "## Typical answers\n\n" + "\n".join(clean) +
    "\n## Every defect\n\n" + ("\n".join(defects) if defects else "None.\n"))
for r in rows:
    print(r)
