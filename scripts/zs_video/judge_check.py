#!/usr/bin/env python3
"""Score a judge against answers whose right label was settled by reading them against the
annotation (results/zero-shot video - 11-17-2026/judge_check.jsonl).

python3 -I scripts/zs_video/judge_check.py --check judge_check.jsonl --units video_units/*.jsonl \
    --host http://127.0.0.1:19123 --judge-id "gpt-oss-120b MXFP4" \
    --judge-temperature 1.0 --judge-top-p 1.0 --reasoning-effort medium \
    --compare gemma-3-27b-it_Q4_K_M

Each case names the answers file (a glob, relative to the run dir), the unit, the label range a
correct judge should give, and for questions that ask when, the localization range. Some cases
also carry `label_ok_alt`: the range under the literal reading of the v5 anchors, where the
right event without its recorded grade or phase is a 3 (see `alt_reading`). The summary counts
both ways. --compare also scores another judge's stored reviews of the same answers.
--stored re-scores a previous run's --out file without calling a judge.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("zr", Path(__file__).with_name("zs_video_run.py"))
zr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zr)


def within(v, rng) -> bool:
    return v is not None and rng[0] <= v <= rng[1]


def either(v, case) -> bool:
    """In range as written, or under the case's second reading if it has one."""
    return within(v, case["label_ok"]) or bool(case.get("label_ok_alt") and within(v, case["label_ok_alt"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", type=Path, required=True)
    ap.add_argument("--units", nargs="+", type=Path, required=True)
    ap.add_argument("--host", default=None)
    ap.add_argument("--judge-id", required=True)
    ap.add_argument("--stored", type=Path, default=None, help="a previous --out file to re-score")
    ap.add_argument("--judge-temperature", type=float, default=0.0)
    ap.add_argument("--judge-top-p", type=float, default=None)
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--served-model", default=None)
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--compare", default=None, help="another judge's id as in its reviews-<id>.jsonl")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    units = {u["unit_id"]: u for u in zr.load_units(args.units)}
    template = zr.text_block(zr.JUDGE_PROMPT)
    cases = zr.read_jsonl(args.check)
    for c in cases:
        for path in sorted(glob.glob(c["answers"])):
            for g in zr.read_jsonl(Path(path)):
                if g["unit_id"] == c["unit_id"] and g.get("answer"):
                    c["gen"], c["path"] = g, path
        if "gen" not in c:
            raise SystemExit(f"no answer for {c['unit_id']} in {c['answers']}")

    if args.stored:
        stored = {(x["answers"], x["unit_id"]): x for x in zr.read_jsonl(args.stored)}

        def run(c):
            x = stored[(c["path"], c["unit_id"])]
            return c, {"label_score": x["label"], "localization_score": x["localization"],
                       "evaluator_notes": x.get("note")}
    else:
        if not args.host:
            ap.error("--host is needed unless --stored is given")

        def run(c):
            return c, zr.judge(units[c["unit_id"]], c["gen"], args, template)

    hits, other_hits, n_loc, loc_hits, other_loc_hits, lines = 0, 0, 0, 0, 0, []
    hits_alt, other_hits_alt = 0, 0
    for c, r in zr.run_pool(run, cases, args.workers):
        ok = within(r.get("label_score"), c["label_ok"])
        hits += ok
        hits_alt += either(r.get("label_score"), c)
        other = None
        if args.compare:
            rev = Path(c["path"]).with_name(f"reviews-{args.compare}.jsonl")
            other = {x["unit_id"]: x for x in zr.read_jsonl(rev)}.get(c["unit_id"], {})
            other_hits += within(other.get("label_score"), c["label_ok"])
            other_hits_alt += either(other.get("label_score"), c)
        if c.get("localization_ok"):
            n_loc += 1
            loc_hits += within(r.get("localization_score"), c["localization_ok"])
            if other is not None:
                other_loc_hits += within(other.get("localization_score"), c["localization_ok"])
        lines.append({"unit_id": c["unit_id"], "answers": c["path"], "label_ok": c["label_ok"],
                      "label": r.get("label_score"), "localization": r.get("localization_score"),
                      "ok": ok, "other_label": other and other.get("label_score"),
                      "note": r.get("evaluator_notes") or r.get("reason"), "why": c["why"]})
        print(f"{'ok ' if ok else 'MISS'} {c['unit_id'][:44]:44} want {c['label_ok']} got {r.get('label_score')}"
              + (f" | {args.compare}: {other.get('label_score')}" if other is not None else "")
              + f" | {(r.get('evaluator_notes') or r.get('reason') or '')[:110]}", flush=True)
    print(f"\n{args.judge_id}: label in range on {hits} of {len(cases)} as written, {hits_alt} "
          f"accepting the literal reading too" + (f"; localization on {loc_hits} of {n_loc}" if n_loc else ""))
    if args.compare:
        print(f"{args.compare}: label in range on {other_hits} of {len(cases)} as written, {other_hits_alt} "
              f"accepting the literal reading too" + (f"; localization on {other_loc_hits} of {n_loc}" if n_loc else ""))
    if args.out:
        args.out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
