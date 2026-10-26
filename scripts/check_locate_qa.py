#!/usr/bin/env python3
"""Check that every JIGSAWS locate question has exactly one answer in its recording.

The builder (build_vts_questions in generate_event_qa.py) is designed to only ask
questions with one answer, but a check that calls the builder only confirms the
builder agrees with itself. This one recomputes each kind's uniqueness from the raw
transcriptions and error labels with code of its own -- the label CSVs are the one
thing it parses with the pipeline's parser. Its first run found four questions the
builder had got wrong.

Per record:

  span         the span text is the clue's frames, formatted
  uniqueness   the record's kind still has exactly one answer:
                 step           the gesture occurs once in the trial
                 step_in_phase  once in that throw, and the trial has the task's
                                number of throws
                 step_nth       the clue is that occurrence of the gesture
                 phase          the clue is that throw
                 event          the only span carrying that error type, with every
                                span that could carry it labelled
                 event_nth      that occurrence among the spans carrying the type
                 worst          the stored ratio and margin pass the thresholds
                 redo           that occurrence of the gesture returned to right
                                after the stored one, counted again from the spans
  flags        not failures, but worth knowing: a gesture labelled twice back to
               back, which reads as one action on screen; a one-gesture throw; an
               error clip carrying a second error type

Then a blind test, the counterpart of the VideoTreeSearch filter that drops
questions a model answers without the video: each answer span is guessed from the
question alone, as the median position of the same question's answer in every other
trial, and scored by IoU. A question the guess finds at IoU >= 0.3 gives its answer
away to anyone who knows the task's structure. plan_locate_qa.py uses the same guess
to drop such questions before any answer is generated.

    python3 scripts/check_locate_qa.py "results/outputs JIGSAWS VTS locate 1297 QA - 10-25-2026"
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from error_labels import load_all, resolve_label_root
from localize_events import read_spans, resolve_jigsaws_root, resolve_task_root

FPS = 30
EXPECTED_THROWS = {"Suturing": 4, "Needle_Passing": 4, "Knot_Tying": 2}
ORDINALS = ("first second third fourth fifth sixth seventh eighth ninth tenth "
            "eleventh twelfth").split()
BLIND_IOU = 0.3


def clock(frame: int) -> str:
    minutes, tenths = divmod(round(frame / FPS * 10), 600)
    return f"{minutes}:{tenths / 10:04.1f}"


def throws_of(task: str, spans: list[tuple[int, int, str]]) -> list[list[tuple[int, int, str]]]:
    """The throws, cut by the rule the builder documents, written out again here."""
    out, cur, started = [], [], task == "Knot_Tying"
    for span in spans:
        if span[2] == "G11":
            break
        if not started and span[2] != "G2":
            continue
        started = True
        if span[2] == "G4" or (task == "Knot_Tying" and span[2] == "G12"):
            if cur:
                out.append(cur)
            cur = [span] if span[2] == "G12" else []
        else:
            cur.append(span)
    if cur:
        out.append(cur)
    return out


def blind_keys(r: dict) -> list[tuple]:
    """What the question tells a model that cannot see the video, most specific first.

    The blind guess uses the first of these that at least BLIND_MIN_OTHERS other
    trials share, so a question too rare for its exact prior ("the ninth time...")
    is still guessed from the coarser one ("the ninth time" of any action) rather
    than let through as if nothing could be guessed.
    """
    k, t, g, rec = r["question_kind"], r["task"], r["grounding"], r["recorded"]
    which = re.search(r"\((\w+) of", rec)
    nth = which.group(1) if which else ""
    if k == "vts_phase":
        keys = [(k, t, rec.split(",")[0])]
    elif k == "vts_step":
        keys = [(k, t, g["gesture"])]
    elif k == "vts_step_in_phase":
        throw = re.search(r"in the (\w+) ", rec).group(1)
        keys = [(k, t, g["gesture"], throw), (k, t, throw)]
    elif k == "vts_step_nth":
        keys = [(k, t, g["gesture"], nth), (k, t, nth)]
    elif k in ("vts_event", "vts_event_nth"):
        keys = [(k, t, g["error_types"][0], nth), (k, t, nth)]
    elif k == "vts_redo":
        keys = [(k, t, g["gesture"], g["after"], nth), (k, t, g["gesture"], g["after"])]
    else:
        keys = []
    return keys + [(k, t)]


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


BLIND_MIN_OTHERS = 3


def blind_ious(records: list[dict]) -> list[float | None]:
    """IoU of a guess made from each question's text alone, per record.

    The guess is the median position, as a share of the recording, of the same
    question's answer in every other trial, scaled to this recording's length. None
    where not even the coarsest key has enough other trials to guess from.
    """
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        for key in blind_keys(r):
            by_key[key].append(r)
    out: list[float | None] = []
    for r in records:
        g = r["grounding"]
        others = next((o for o in ([x for x in by_key[key] if x["trial_id"] != r["trial_id"]]
                                   for key in blind_keys(r))
                       if len(o) >= BLIND_MIN_OTHERS), None)
        if others is None:
            out.append(None)
            continue
        guess = tuple(statistics.median(o["grounding"][f] / FPS / o["grounding"]["trial_s"]
                                        for o in others) * g["trial_s"]
                      for f in ("clue_start_frame", "clue_end_frame"))
        out.append(iou(guess, (g["clue_start_frame"] / FPS, g["clue_end_frame"] / FPS)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch", help="a batch directory holding qa_records.jsonl files")
    ap.add_argument("--jigsaws-root", default=None)
    args = ap.parse_args()

    records = [json.loads(line) for path in sorted(Path(args.batch).rglob("qa_records.jsonl"))
               for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    root = resolve_jigsaws_root(args.jigsaws_root)
    rows: dict[tuple[str, int, int], dict[str, bool]] = defaultdict(dict)
    scored: dict[tuple[str, str], set[str]] = defaultdict(set)
    for lab in load_all(resolve_label_root()):
        rows[lab["trial_id"], lab["start_frame"], lab["end_frame"]][lab["error_type"]] = \
            lab["is_error"]
        scored[lab["task"], lab["error_type"]].add(lab["gesture"])

    span_cache: dict[str, list[tuple[int, int, str]]] = {}
    failures, flags = [], Counter()
    for r in records:
        task, trial, kind, g = r["task"], r["trial_id"], r["question_kind"], r["grounding"]
        if trial not in span_cache:
            span_cache[trial] = read_spans(resolve_task_root(root, task), trial)
        spans = span_cache[trial]
        s, e = g["clue_start_frame"], g["clue_end_frame"]
        if f"{clock(s)}-{clock(e)}" != r["span"]:
            failures.append((r["qa_id"], "span text is not the clue's frames"))
        gesture = g.get("gesture")
        same = [x for x in spans if x[2] == gesture]
        if kind == "vts_step":
            ok = len(same) == 1 and same[0][:2] == (s, e)
        elif kind == "vts_step_nth":
            i = g["occurrence"]
            ok = len(same) > 1 and len(same) >= i and same[i - 1][:2] == (s, e)
        elif kind == "vts_step_in_phase":
            throws = throws_of(task, spans)
            k = next((n for n, t in enumerate(throws, 1) if any(x[:2] == (s, e) for x in t)),
                     None)
            ok = (len(throws) == EXPECTED_THROWS[task] and k is not None
                  and sum(x[2] == gesture for x in throws[k - 1]) == 1
                  and f"in the {ORDINALS[k - 1]} " in r["recorded"])
        elif kind == "vts_phase":
            throws = throws_of(task, spans)
            k = g["throw"]
            ok = (len(throws) == EXPECTED_THROWS[task]
                  and (throws[k - 1][0][0], throws[k - 1][-1][1]) == (s, e))
            if len(throws[k - 1]) < 2:
                flags["a throw of one gesture"] += 1
        elif kind in ("vts_event", "vts_event_nth"):
            t = g["error_types"][0]
            carrying = [x for x in spans if rows.get((trial, x[0], x[1]), {}).get(t)]
            covered = all(t in rows.get((trial, x[0], x[1]), {})
                          for x in spans if x[2] in scored[task, t])
            i = g["occurrence"]
            ok = (covered and len(carrying) >= i and carrying[i - 1][:2] == (s, e)
                  and (len(carrying) == 1) == (kind == "vts_event"))
            if len(g["error_types"]) > 1:
                flags["an error clip carrying a second error type"] += 1
        elif kind == "vts_redo":
            returns = [c[:2] for a, b, c in zip(spans, spans[1:], spans[2:])
                       if a[2] == c[2] == gesture and b[2] == g["after"]]
            i = g["occurrence"]
            ok = (len(returns) == g["occurrences"] and len(returns) >= i
                  and returns[i - 1] == (s, e)
                  and not any(a[2] == b[2] == gesture for a, b in zip(spans, spans[1:])))
        else:
            ok = g["ratio"] >= 2.0 and (g["runner_up_ratio"] is None
                                        or g["runner_up_ratio"] <= 0.8 * g["ratio"])
        if not ok:
            failures.append((r["qa_id"], "more or fewer than one answer"))
        if gesture and kind != "vts_phase":
            idx = next(n for n, x in enumerate(spans) if x[:2] == (s, e))
            if any(0 <= j < len(spans) and spans[j][2] == gesture for j in (idx - 1, idx + 1)):
                flags["a gesture labelled twice back to back"] += 1

    print(f"{len(records)} records, {len(failures)} with a uniqueness failure")
    for qa_id, why in failures[:20]:
        print(f"  FAIL {qa_id}: {why}")
    for flag, n in flags.most_common():
        print(f"  flag: {flag}: {n}")

    per_kind: dict[str, list[float | None]] = defaultdict(list)
    for r, score in zip(records, blind_ious(records)):
        per_kind[r["question_kind"]].append(score)
    print(f"\nblind test: answer guessed from the question alone, found at IoU >= {BLIND_IOU}")
    total = found = 0
    for kind, scores in sorted(per_kind.items(), key=lambda kv: -len(kv[1])):
        hit = sum(x is not None and x >= BLIND_IOU for x in scores)
        total, found = total + len(scores), found + hit
        print(f"  {kind:20s} {hit:5d} of {len(scores):5d}  ({hit / len(scores):4.0%})")
    print(f"  {'all':20s} {found:5d} of {total:5d}  ({found / total:4.0%})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
