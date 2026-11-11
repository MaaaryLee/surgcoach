#!/usr/bin/env python3
"""Coaching questions (Template D, adapted) over MultiBypass140's adverse-event labels.

MultiBypass140 rates no skill. It has per-second phase and step labels, and surgeon-annotated
intraoperative adverse events (iAEs), each with a category and a grade on the SEVERE index.
Lavanchy et al. (Surg Endosc 2025, Table 1) give every grade a description, and answers use
those descriptions, never the number. The questions, as revised after the review of 2026-11-04:

  D1  overall feedback on one phase: every event that began in it, each by its grade's
      description, step, time and how long it lasted
  D2  which event to address first: asked only when exactly one event in the phase is at
      SEVERE grade 4 or 5 and the phase has at least one other event, else skipped
  D5  which phase went best: asked only when, of the phases where most of the series'
      operations record an event (gastric pouch creation and gastrojejunal anastomosis),
      exactly one has no event above grade 2 and every other has one at grade 3 or above

How the labels are read (checked in results/mb140 annotation survey - 11-04-2026):

  - Events are rebuilt per Event_ID. Where two events overlap, a second carries both events'
    category and grade flags. So each event's category and grade come from its own seconds,
    or, for the 94 events with no second of their own, from what the shared seconds carry
    beyond the other event's. The rebuilt series matches Lavanchy et al. Table 2 exactly, by
    center, category and grade (797 events).
  - 47 pairs of events cover exactly the same seconds: always a bleeding with an injury. Each
    pair is one incident with both descriptions, never cause and effect.
  - The labels code ischemic injury 1-2 and insufficient closure 1, where the SEVERE index
    grades them 3 and 5, and 5. Codes are mapped to SEVERE grades, which Table 2 confirms.
  - An event belongs to the phase it began in, and is described with every step it ran
    through and any phase it ran on into.
  - "Bleeding - rectified" is 0 on every second, so nothing is said about how an event ended.
  - BBP04 is included. Its video (re-released 2025-09-09) lasts 4480.8 s, which matches its
    phase annotation (ends at 4480.8 s) and its labels (last second 4479).

No answer gives a remedy, since none is annotated. Answers pass pattern checks against the
evidence and a second model call that audits every sentence; that reviewer is itself tested
on known paragraphs at the start of each run. Every record carries its span and event
timestamps, and qa_slim.json keeps only those, the question and the answer.

Dry run (no model; writes the prompts):
  python3 scripts/run_mb140_coaching_d.py --iae-root <labels root> --tables <tables dir> \\
      --output out/qa_records.jsonl --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import pickle
import random
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

CATEGORIES = ["Bleeding", "Mechanical injury", "Thermal injury", "Ischemic injury",
              "Insufficient closure of anastomosis"]
NOUN = {"Bleeding": "bleeding", "Mechanical injury": "mechanical injury", "Thermal injury": "thermal injury",
        "Ischemic injury": "ischemic injury",
        "Insufficient closure of anastomosis": "insufficient closure of the anastomosis"}
# The labels' grade code for each category, as a SEVERE grade (Lavanchy et al. 2025, Table 1:
# ischemic injury has grades 3 and 5 only, insufficient closure grade 5 only).
SEVERE_GRADE = {"Ischemic injury": {1: 3, 2: 5}, "Insufficient closure of anastomosis": {1: 5}}
# Lavanchy et al. 2025 (Surg Endosc 39:2026-2036, open access), Table 1, SEVERE column, verbatim.
SEVERE_DESCRIPTION = {
    "Bleeding": {1: "Very low amount of blood lost", 2: "Low amount of blood lost",
                 3: "Intermediate amount of blood lost", 4: "High amount of blood lost",
                 5: "Very high amount of blood lost"},
    "Thermal injury": {1: "Superficial penetration to “less vital” tissue",
                       2: "Deep penetration to “less vital” tissue or any organ/tissue subjected to planned resection",
                       3: "Superficial penetration to “vital” tissue",
                       4: "Deep penetration to “vital” tissue to the level of muscularis/parenchyma",
                       5: "Through and through injury to hollow organ or deeper parenchymal injury to solid organ"},
    "Mechanical injury": {1: "Superficial penetration to “less vital” tissue, needle poke to tissue",
                          2: "Full-thickness injury",
                          3: "Superficial penetration to “vital” tissue",
                          4: "Deep penetration to “vital” tissue",
                          5: "Through and through injury to “vital” tissue"},
    "Ischemic injury": {3: "Sign of ischemia with indeterminate nature", 5: "Sign of permanent tissue ischemia"},
    "Insufficient closure of anastomosis": {5: "All insufficient closure of anastomosis"},
}
# Phases with no surgical content of their own: other_intervention, out_of_body, severe_index.
# (The per-second labels never use 12 or 13; severe_index spans in the per-video JSON only mark
# event spans, 332 of 334 within a second of one.)
SKIPPED_PHASES = {11, 12, 13}
EXCLUDED_VIDEOS: set[str] = set()
MIN_SPAN_SECONDS = 120
MAX_INCIDENTS = {"D1": 5, "D2": 6}
# The 11-04 review: D2 only when one event alone is at grade 4 or 5; D5 only when one phase alone
# has nothing above grade 2.
HIGH, LOW = 4, 2
PER_TEMPLATE = {"D1": 15, "D2": 15, "D5": 15}
# D5's phases: those where at least this share of the series' operations record an adverse
# event, gastric pouch creation (124 of 136) and gastrojejunal anastomosis (99 of 139).
D5_BASE_RATE = 0.6
SEED = 20261028

QUESTIONS = {
    # The JIGSAWS D1 wording with its four rated areas removed, since none is annotated here.
    "D1": ("If you were the supervising surgeon, based only on the events occurring during [span], "
           "what feedback would you give on the trainee's performance?"),
    # The JIGSAWS D2 wording with its areas replaced by the annotated events, and without
    # "what to do immediately", since no remedy is annotated.
    "D2": ("Given the trainee's performance during [span], which adverse event should the trainee "
           "address first, and why?"),
    # The JIGSAWS D5 wording ("what is their strongest technical skill, and why does it matter?")
    # with the skill replaced by a phase, since the labels rate no skill; [span] is the operation.
    "D5": ("Based on the events occurring during [span], which phase went best for the trainee, and "
           "why does it matter?"),
}


def max_words(unit: dict[str, Any]) -> int:
    n = len(unit["incidents"])
    if unit["template_id"] == "D1":
        return 50 if n == 1 else 65 if n == 2 else 80
    if unit["template_id"] == "D2":
        return 65
    return 55 if n == 0 else 65


def hms(seconds: int) -> str:
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}"


def duration_text(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{s} s" if not m else f"{m} min" + (f" {s} s" if s else "")


def load_names(tables: Path) -> tuple[dict[int, str], dict[int, str]]:
    """Phase and step names from the dataset's own tables, made readable but not reworded."""
    def read(name: str, strip_prefix: bool) -> dict[int, str]:
        out = {}
        with (tables / name).open() as handle:
            for row in csv.reader(handle):
                if not row or not row[0].strip().isdigit():
                    continue
                text = row[1].strip()
                if strip_prefix:
                    text = re.sub(r"^s\d+_", "", text)
                out[int(row[0])] = re.sub(r"^his ", "His ", text.replace("_", " "))
        return out
    return read("phase.csv", False), read("step.csv", True)


def load_videos(iae_root: Path) -> dict[str, list[dict[str, Any]]]:
    videos: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(iae_root.glob("*/labels/*/*_with_iae.pickle")):
        with path.open("rb") as handle:
            for video_id, frames in pickle.load(handle).items():
                videos.setdefault(video_id, frames)
    return videos


def frame_findings(frame: dict[str, Any]) -> set[tuple[str, int]]:
    """The (category, grade code) pairs flagged on one second."""
    found = set()
    for category in CATEGORIES:
        if frame.get(category) != 1:
            continue
        for key, value in frame.items():
            if value == 1 and key.startswith(category + " - "):
                code = key.rsplit(" - ", 1)[1]
                if code.isdigit() and int(code) > 0:
                    found.add((category, int(code)))
    return found


def events_of(frames: list[dict[str, Any]], steps: dict[int, str]) -> list[dict[str, Any]]:
    """One record per Event_ID, with its category and grade resolved where events overlap.

    A second shared by two events carries both events' category and grade flags (5,444 of the
    series' 5,797 shared seconds carry two of each; the rest carry one, for two events of the
    same category and grade). An event's finding is read from its own seconds; an event with
    none takes what its seconds carry beyond the findings of the events it shares them with.
    Two such events over exactly the same seconds share out what is left, in Event_ID order:
    which ID holds which finding cannot be told, and does not matter, as both cover one span.
    """
    seconds: dict[str, list[int]] = defaultdict(list)
    for i, frame in enumerate(frames):
        for event_id in frame.get("Event_ID") or []:
            seconds[event_id].append(i)
    finding: dict[str, tuple[str, int]] = {}
    source: dict[str, str] = {}
    for event_id, idx in seconds.items():
        own = Counter(f for i in idx if len(frames[i]["Event_ID"]) == 1 for f in frame_findings(frames[i]))
        if own:
            finding[event_id], source[event_id] = own.most_common(1)[0][0], "its own seconds"
    for _ in range(3):
        for event_id, idx in seconds.items():
            if event_id in finding:
                continue
            twins = sorted(o for o, ix in seconds.items() if o not in finding and ix == idx)
            left: Counter[tuple[str, int]] = Counter()
            for i in idx:
                known = {finding[o] for o in frames[i]["Event_ID"] if o in finding}
                left.update(frame_findings(frames[i]) - known)
            rest = sorted(f for f, _ in left.most_common(len(twins)))
            if rest:
                for k, other in enumerate(twins):
                    finding[other] = rest[min(k, len(rest) - 1)]
                    source[other] = "the seconds it shares, beyond the other event's finding"
            else:
                outer = Counter(finding[o] for i in idx for o in frames[i]["Event_ID"] if o in finding)
                if outer:
                    finding[event_id] = outer.most_common(1)[0][0]
                    source[event_id] = "the event it lies within, whose finding it shares"
    events = []
    for event_id, idx in seconds.items():
        if event_id not in finding:
            raise ValueError(f"event {event_id} has no category or grade the labels resolve")
        category, code = finding[event_id]
        grade = SEVERE_GRADE.get(category, {}).get(code, code)
        runs: list[list[int]] = []
        for i in idx:
            step = int(frames[i]["Step_gt"])
            if runs and runs[-1][0] == step:
                runs[-1][1] += 1
            else:
                runs.append([step, 1])
        phase_ids = list(dict.fromkeys(int(frames[i]["Phase_gt"]) for i in idx))
        events.append({
            "event_id": event_id,
            "category": category,
            "label_code": code,
            "severity": grade,
            "description": SEVERE_DESCRIPTION[category][grade],
            "start_s": int(frames[idx[0]]["Original_frame_id"]),
            "end_s": int(frames[idx[-1]]["Original_frame_id"]),
            "step_id": runs[0][0],
            "step": steps.get(runs[0][0], "null step"),
            "step_runs": [{"step_id": s, "step": steps.get(s, "null step"), "seconds": n} for s, n in runs],
            "step_ids": list(dict.fromkeys(s for s, _ in runs)),
            "phase_id": phase_ids[0],
            "phase_ids": phase_ids,
            "shares_frames": any(len(frames[i]["Event_ID"]) > 1 for i in idx),
            "finding_from": source[event_id],
        })
    return sorted(events, key=lambda e: (e["start_s"], e["end_s"], e["event_id"]))


def incidents_of(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events over exactly the same seconds form one incident: in this series, always a bleeding
    recorded with an injury. The injury is listed first."""
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        groups[(event["start_s"], event["end_s"])].append(event)
    incidents = []
    for (start, end), members in sorted(groups.items()):
        members = sorted(members, key=lambda e: (e["category"] == "Bleeding", e["event_id"]))
        lead = members[0]
        incidents.append({"start_s": start, "end_s": end, "events": members,
                          "severity": max(e["severity"] for e in members),
                          **{k: lead[k] for k in ("step_id", "step", "step_runs", "step_ids", "phase_id", "phase_ids")}})
    for i, x in enumerate(incidents):
        x["began_during"] = next((j for j in range(i) if incidents[j]["end_s"] >= x["start_s"]), None)
        x["began_with"] = x["began_during"] is not None and incidents[x["began_during"]]["start_s"] == x["start_s"]
    return incidents


def phase_segments(frames: list[dict[str, Any]], phases: dict[int, str]) -> list[dict[str, Any]]:
    segments, start = [], 0
    for i in range(1, len(frames) + 1):
        if i == len(frames) or frames[i]["Phase_gt"] != frames[start]["Phase_gt"]:
            pid = int(frames[start]["Phase_gt"])
            begin, end = int(frames[start]["Original_frame_id"]), int(frames[i - 1]["Original_frame_id"])
            if pid not in SKIPPED_PHASES and end - begin >= MIN_SPAN_SECONDS:
                segments.append({"phase_id": pid, "phase": phases.get(pid, str(pid)), "start_s": begin, "end_s": end})
            start = i
    return segments


def phase_pieces(frames: list[dict[str, Any]]) -> dict[int, list[tuple[int, int]]]:
    """Every contiguous run of each surgical phase, however short, as (first, last) seconds."""
    pieces: dict[int, list[tuple[int, int]]] = defaultdict(list)
    start = 0
    for i in range(1, len(frames) + 1):
        if i == len(frames) or frames[i]["Phase_gt"] != frames[start]["Phase_gt"]:
            pid = int(frames[start]["Phase_gt"])
            if pid not in SKIPPED_PHASES:
                pieces[pid].append((int(frames[start]["Original_frame_id"]), int(frames[i - 1]["Original_frame_id"])))
            start = i
    return pieces


def center_of(video_id: str) -> str:
    return "Bern" if video_id.startswith("BBP") else "Strasbourg"


def d5_candidates(videos: dict[str, list[dict[str, Any]]], phases: dict[int, str],
                  steps: dict[int, str]) -> tuple[list[dict[str, Any]], dict[int, tuple[int, int]]]:
    """Operations where one commonly eventful phase alone has nothing above grade 2.

    A phase is present in an operation when its pieces add up to MIN_SPAN_SECONDS. Its worst
    grade is the highest SEVERE grade of any event touching it, 0 when none does. The base rate
    is the share of operations with the phase that record an event in it. D5's phase is the only
    common phase of the operation with a worst grade of LOW or less, so every other common phase
    has an event above LOW; it lies in one piece, and its events lie wholly inside it.
    """
    info: dict[str, dict[str, Any]] = {}
    for video_id, frames in videos.items():
        if video_id in EXCLUDED_VIDEOS:
            continue
        events = events_of(frames, steps)
        phase_at = {int(f["Original_frame_id"]): int(f["Phase_gt"]) for f in frames}
        present = {pid: ps for pid, ps in phase_pieces(frames).items()
                   if sum(b - a + 1 for a, b in ps) >= MIN_SPAN_SECONDS}
        worst = dict.fromkeys(present, 0)
        for e in events:
            for second in range(e["start_s"], e["end_s"] + 1):
                if phase_at.get(second) in worst:
                    worst[phase_at[second]] = max(worst[phase_at[second]], e["severity"])
        info[video_id] = {"present": present, "worst": worst, "events": events,
                          "span": (int(frames[0]["Original_frame_id"]), int(frames[-1]["Original_frame_id"]))}
    rates: dict[int, tuple[int, int]] = {}
    for pid in phases:
        have = [v["worst"][pid] for v in info.values() if pid in v["present"]]
        if have:
            rates[pid] = (sum(w > 0 for w in have), len(have))
    common = sorted(pid for pid, (m, n) in rates.items() if m / n >= D5_BASE_RATE)
    units = []
    for video_id, v in info.items():
        here = [pid for pid in common if pid in v["present"]]
        best = [pid for pid in here if v["worst"][pid] <= LOW]
        if len(here) < 2 or len(best) != 1 or len(v["present"][best[0]]) != 1:
            continue
        pid = best[0]
        (start, end), = v["present"][pid]
        touching = [e for e in v["events"] if e["start_s"] <= end and e["end_s"] >= start]
        if any(e["start_s"] < start or e["end_s"] > end for e in touching):
            continue
        mine = v["worst"][pid]
        worse = sum(w["worst"][pid] > mine for w in info.values() if pid in w["present"])
        units.append({"video_id": video_id, "center": center_of(video_id), "phase_id": pid,
                      "phase": phases.get(pid, str(pid)), "start_s": start, "end_s": end,
                      "incidents": incidents_of(touching), "worst": mine,
                      "base_rate": (worse, rates[pid][1]),
                      "common_phases": {phases.get(p, str(p)): v["worst"][p] for p in here},
                      "operation_start_s": v["span"][0], "operation_end_s": v["span"][1]})
    return units, rates


def build_candidates(videos: dict[str, list[dict[str, Any]]], phases: dict[int, str],
                     steps: dict[int, str]) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = {"D1": [], "D2": [], "D5": d5_candidates(videos, phases, steps)[0]}
    for video_id, frames in videos.items():
        if video_id in EXCLUDED_VIDEOS:
            continue
        incidents = incidents_of(events_of(frames, steps))
        for seg in phase_segments(frames, phases):
            # A phase's events are those that began in it; they may run on past its end. A phase
            # with an event still running from before is skipped, as that event belongs to
            # the phase before (3 phases of the series).
            began = [x for x in incidents if seg["start_s"] <= x["start_s"] <= seg["end_s"]]
            if not began or any(x["start_s"] < seg["start_s"] <= x["end_s"] for x in incidents):
                continue
            unit = {"video_id": video_id, "center": center_of(video_id), **seg, "incidents": began}
            if len(began) <= MAX_INCIDENTS["D1"]:
                candidates["D1"].append(unit)
            high = [x for x in began if x["severity"] >= HIGH]
            if 2 <= len(began) <= MAX_INCIDENTS["D2"] and len(high) == 1:
                candidates["D2"].append({**unit, "focus": began.index(high[0])})
    return candidates


def select(candidates: dict[str, list[dict[str, Any]]], per_template: dict[str, int]) -> list[dict[str, Any]]:
    """per_template of each, one question per surgery, alternating centers, varied phases.

    D1 cycles through a one-event phase, a phase with several, and one with an injury and a
    bleeding recorded together; D5 alternates a phase with no event and one with mild ones.
    """
    rng = random.Random(SEED)
    used: set[str] = set()
    chosen = []
    kinds = {"D1": ["single", "several", "pair", "several"], "D5": ["clean", "mild"]}
    def kind(unit: dict[str, Any]) -> str:
        if unit["template_id_hint"] == "D5":
            return "clean" if not unit["incidents"] else "mild"
        if any(len(x["events"]) > 1 for x in unit["incidents"]):
            return "pair"
        return "single" if len(unit["incidents"]) == 1 else "several"
    for template in ("D2", "D5", "D1"):  # scarcest spans first
        if not per_template.get(template):
            continue
        pool = [{**u, "template_id_hint": template} for u in candidates[template]]
        rng.shuffle(pool)
        picked, phase_count = [], defaultdict(int)
        for want_center in ["Bern", "Strasbourg"] * per_template[template]:
            if len(picked) == per_template[template]:
                break
            options = [u for u in pool if u["center"] == want_center and u["video_id"] not in used]
            if template in kinds:
                wanted = kinds[template][len(picked) % len(kinds[template])]
                options = [u for u in options if kind(u) == wanted] or options
            if not options:
                continue
            best = min(options, key=lambda u: phase_count[u["phase_id"]])
            unit = {k: v for k, v in best.items() if k != "template_id_hint"}
            picked.append({**unit, "template_id": template})
            used.add(best["video_id"])
            phase_count[best["phase_id"]] += 1
            pool.remove(best)
        chosen += picked
    return chosen


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def plain(description: str) -> str:
    """A description with its quotation marks dropped, for matching and for the self-test."""
    return description.replace("“", "").replace("”", "").replace('"', "")


NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
                11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
                17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty"}


def spoken_duration(seconds: int) -> str:
    """A duration as a speaker would round it. Job 42235 drafts said "just over a minute" for
    92 s and "over just over six minutes"; job 42234's said "about three minutes and twenty-two
    seconds"."""
    d = int(seconds)
    if d < 15:
        return f"{d} seconds"
    if d < 50:
        return f"about {5 * round(d / 5)} seconds"
    if d < 80:
        return "about a minute"
    if d < 106:
        return "about a minute and a half"
    # Nearest half minute, ties down (3 min 15 s is "about three minutes"); from 106 s that is
    # two minutes or more.
    whole, half = divmod(int(d / 30 + 0.49), 2)
    word = NUMBER_WORDS.get(whole, str(whole))
    return f"about {word} and a half minutes" if half else f"about {word} minutes"


# Published descriptions as grammatical phrases, every grade-carrying word kept: bleeding as a
# clause ("a very low amount of blood was lost"), injuries as a noun phrase. Job 42235's drafts
# pasted the noun phrases in bare ("a mechanical injury with superficial penetration", "both
# where a very low amount of blood was lost").
SAY = {
    ("Mechanical injury", 1): "a superficial penetration to less vital tissue, or a needle poke to tissue",
    ("Thermal injury", 2): "a deep penetration to less vital tissue, or to any organ or tissue subjected to planned resection",
    ("Thermal injury", 4): "a deep penetration to vital tissue, to the level of the muscularis or parenchyma",
    ("Insufficient closure of anastomosis", 5): "an insufficient closure of the anastomosis",
}


def say_finding(e: dict[str, Any]) -> str:
    key = (e["category"], e["severity"])
    if key in SAY:
        return SAY[key]
    text = plain(e["description"])
    phrase = text[0].lower() + text[1:]
    if e["category"] == "Bleeding":
        amount = phrase[:-len(" lost")]
        return ("an " if amount[0] in "aeiou" else "a ") + amount + " was lost"
    return ("an " if phrase[0] in "aeiou" else "a ") + phrase


def say_incident(x: dict[str, Any]) -> str:
    """The incident in words the answer can use as they stand, as the subject of a sentence.
    Job 42236's "a bleed in which a high amount of blood was lost and lasted about four minutes"
    showed a relative clause does not take a verb after it, so a bleed is "a bleed with ... lost"."""
    parts = []
    for e in x["events"]:
        if e["category"] == "Bleeding":
            parts.append("a bleed with " + say_finding(e).replace(" was lost", " lost"))
        elif e["category"] == "Insufficient closure of anastomosis":
            parts.append(say_finding(e))
        else:
            parts.append(f"a {NOUN[e['category']]}, {say_finding(e)}")
    return ", and with it ".join(parts)


def incident_text(x: dict[str, Any]) -> str:
    found = [f"{NOUN[e['category']]}, \"{plain(e['description'])}\"" for e in x["events"]]
    if len(found) == 1:
        return found[0]
    return " and ".join(found) + ", recorded together over the same seconds"


def course_text(x: dict[str, Any], phases: dict[int, str]) -> str:
    later, seen = [], {x["step_id"]}
    for run in x["step_runs"][1:]:
        later.append(("back to " if run["step_id"] in seen else "") + f"\"{run['step']}\"")
        seen.add(run["step_id"])
    if x["step_id"] == 0:
        text = "It began with no step annotated"
    elif later:
        text = f"It began during the step \"{x['step']}\""
    else:
        text = f"It began and ended during the step \"{x['step']}\""
    if later:
        text += " and ran on through " + ", then ".join(later)
    if len(x["phase_ids"]) > 1:
        text += (", and on into the " + ", then ".join(f"\"{phases.get(p, str(p))}\"" for p in x["phase_ids"][1:])
                 + " phase")
    return text + "."


def event_line(n: int, x: dict[str, Any], phases: dict[int, str]) -> str:
    d = x["end_s"] - x["start_s"] + 1
    line = (f"  {n}. From {hms(x['start_s'])} to {hms(x['end_s'])} ({duration_text(d)}; say {spoken_duration(d)}): "
            f"{incident_text(x)}. Say it as: {say_incident(x)}. {course_text(x, phases)}")
    if x.get("began_during") is not None:
        k = x["began_during"]
        line += (f" It began at the same second as event {k + 1}." if x.get("began_with")
                 else f" It began while event {k + 1} was still going.")
    return line


def lead_of(incidents: list[dict[str, Any]]) -> int | None:
    """The incident D1 may open with as the most severe: the only one at grade HIGH or above, or,
    when every event in the phase is of one category, the only one at the top grade. Grades of
    different categories are otherwise not ranked against each other."""
    if len(incidents) < 2:
        return None
    high = [i for i, x in enumerate(incidents) if x["severity"] >= HIGH]
    if len(high) == 1:
        return high[0]
    if len({e["category"] for x in incidents for e in x["events"]}) == 1:
        top = max(x["severity"] for x in incidents)
        at_top = [i for i, x in enumerate(incidents) if x["severity"] == top]
        if len(at_top) == 1:
            return at_top[0]
    return None


def same_description_groups(incidents: list[dict[str, Any]]) -> list[list[int]]:
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, x in enumerate(incidents):
        groups[tuple((e["category"], e["severity"]) for e in x["events"])].append(i)
    return [g for g in groups.values() if len(g) > 1]


def plan_lines(unit: dict[str, Any], phases: dict[int, str]) -> list[str]:
    incidents, n = unit["incidents"], len(unit["incidents"])
    never = ("Never give a grade as a number, name the grading index or a scale, or use other words for "
             "how serious it was.")
    if unit["template_id"] == "D5":
        m, total = unit["base_rate"]
        if not incidents:
            what = "and that no adverse event was recorded in it"
        elif n == 1:
            what = "and what its only adverse event was, as its Say it as wording gives it, with its step and start time"
        elif len(same_description_groups(incidents)) == 1 and len(same_description_groups(incidents)[0]) == n:
            first = incidents[0]["events"][0]
            times = ", ".join(hms(x["start_s"]) for x in incidents[:-1]) + f" and {hms(incidents[-1]['start_s'])}"
            plural = ("bleeds" if first["category"] == "Bleeding" else
                      NOUN[first["category"]][:-1] + "ies" if NOUN[first["category"]].endswith("y")
                      else NOUN[first["category"]] + "s")
            what = (f"and what its {n} adverse events were, with their start times, as in \"its "
                    f"{NUMBER_WORDS.get(n, n)} {plural}, at {times}, each "
                    + ("with " + say_finding(first).replace(" was lost", " lost")
                       if first["category"] == "Bleeding" else say_finding(first)) + "\"")
        else:
            what = (f"and what its {n} adverse events were, as their Say it as wording gives them, with their start "
                    "times")
        return [
            f"Write two short sentences, under {max_words(unit)} words, like a supervising surgeon telling the "
            "trainee what went best.",
            f"Say that the {unit['phase']} phase, from {hms(unit['start_s'])} to {hms(unit['end_s'])}, went best, "
            f"{what}.",
            f"Why it matters: give WHY IT MATTERS, {m} of {total}. Give no other reason.",
            "The events say nothing about skill: no praise of how the trainee worked, no causes, no consequences "
            "and no advice. Say nothing about the other phases. " + never,
        ]
    if unit["template_id"] == "D1":
        lines = [f"Write overall feedback in {'one or two' if n == 1 else 'two or three'} short sentences, under "
                 f"{max_words(unit)} words, like a supervising surgeon summing up the {unit['phase']} phase for the "
                 "trainee."]
        lead = lead_of(incidents)
        if lead is not None:
            lines.append(f"Start with event {lead + 1} and say it was the most severe in this phase (\"The most "
                         "severe was ...\"), then give the others in brief.")
        elif n > 1:
            kinds = {e["category"] for x in incidents for e in x["events"]}
            why = ("their kinds are graded on different scales" if len(kinds) > 1
                   else "more than one shares the highest grade")
            lines.append(f"Take the events in time order, and do not call any one of them the most severe: {why}.")
        for group in same_description_groups(incidents):
            names = " and ".join(str(i + 1) for i in group)
            lines.append(f"Events {names} have the same description: you may give it once for them together.")
        lines += [
            "Describe each event as its Say it as wording gives it, with its step and start time. You may say how "
            "long it lasted, as its say duration gives it, and which steps it ran on through.",
            "This is overall feedback on what happened, not a priority: do not tell the trainee to address or note "
            "anything. End on the events themselves: no closing line about what was or was not recorded.",
            "No causes, consequences, judgments of skill or advice. " + never,
        ]
        return lines
    focus = incidents[unit["focus"]]
    noun = " and ".join(NOUN[e["category"]] for e in focus["events"])
    return [
        f"Write two or three short sentences, under {max_words(unit)} words, like a supervising surgeon giving the "
        "trainee one clear priority.",
        f"Name event {unit['focus'] + 1} as the one to address first, with its step and start time, as in "
        f"\"You should address the {noun} at {hms(focus['start_s'])}, during {focus['step']}, first.\"",
        f"Why: it was the most severe of the {n} events in this phase. Say what it was, as its Say it as wording "
        "gives it; you may add how long it lasted, as its say duration gives it, and the steps it ran on through. "
        "Give no other reason.",
        "No causes, consequences, judgments of skill or advice, and nothing on how to address it. " + never,
    ]


def build_prompt(unit: dict[str, Any], phases: dict[int, str]) -> tuple[str, str]:
    span = f"{hms(unit['start_s'])}-{hms(unit['end_s'])}"
    header = ("EVENTS (surgeon-annotated intraoperative adverse events, each with its grade's description "
              "on the SEVERE index, as published, in quotes)")
    events = [event_line(i + 1, x, phases) for i, x in enumerate(unit["incidents"])]
    if unit["template_id"] == "D5":
        m, total = unit["base_rate"]
        operation = f"{hms(unit['operation_start_s'])}-{hms(unit['operation_end_s'])}"
        question = QUESTIONS["D5"].replace("[span]", operation)
        # Job 42235's SBP51 drafts could not reword "it recorded an adverse event more severe than
        # any in this one" without echoing it or calling it a rating, so it reads as said aloud.
        why = (f"  In {m} of the {total} operations in this series that include the {unit['phase']} phase, at "
               "least one adverse event was recorded in it." if not unit["incidents"] else
               f"  In {m} of the {total} operations in this series that include the {unit['phase']} phase, that "
               "phase had a more severe adverse event than " + ("yours." if len(unit["incidents"]) == 1
                                                                 else "any of yours."))
        return question, "\n".join([
            "PROCEDURE: laparoscopic Roux-en-Y gastric bypass (MultiBypass140, "
            f"{unit['center']} center), video {unit['video_id']}, from {operation}",
            "",
            "QUESTION",
            question,
            "",
            "ANSWER PLAN",
            *plan_lines(unit, phases),
            "",
            f"PHASE: {unit['phase']}, from {hms(unit['start_s'])} to {hms(unit['end_s'])}",
            *(["EVENTS IN THIS PHASE: none. No surgeon-annotated intraoperative adverse event was recorded in it."]
              if not unit["incidents"] else ["EVENTS IN THIS PHASE " + header[len("EVENTS "):], *events]),
            "",
            "WHY IT MATTERS (counted across all the operations in this series, this one included)",
            why,
        ])
    question = QUESTIONS[unit["template_id"]].replace("[span]", span)
    return question, "\n".join([
        "PROCEDURE: laparoscopic Roux-en-Y gastric bypass (MultiBypass140, "
        f"{unit['center']} center), video {unit['video_id']}",
        f"PHASE: {unit['phase']}, from {hms(unit['start_s'])} to {hms(unit['end_s'])}",
        "",
        "QUESTION",
        question,
        "",
        "ANSWER PLAN",
        *plan_lines(unit, phases),
        "",
        header,
        *events,
    ])


# ---------------------------------------------------------------------------
# Answer checks
# ---------------------------------------------------------------------------

TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
GRADE_NUMBER = re.compile(r"\b(?:severity|grade[sd]?|level)\s+(?:of\s+)?\d\b|\b\d\s+(?:of|out of)\s+(?:5|five)\b", re.I)
GRADE_TALK_WORDS = re.compile(r"\b(?:severity|grade[sd]?|grading|scales?|scor(?:e|es|ed|ing)|index)\b", re.I)
# "SEVERE" as the index's name is caught in capitals only, so that "most severe" passes.
INDEX_NAME = re.compile(r"\bSEVERE\b")
SEVERITY_WORDS = re.compile(
    r"\b(?:mild\w*|moderate\w*|minor|major|significant\w*|serious\w*|small|large|massive|heavy|heavily|"
    r"slight\w*|life-threatening|catastrophic|critical|trivial|substantial\w*|brisk|profuse\w*|minimal\w*|"
    r"negligible|considerable|extensive|limited|modest)\b|(?<!most )(?<!more )(?<!less )\bsevere\b", re.I)
# Words of the descriptions that carry a grade, and the (category, SEVERE grade) each may describe.
DESCRIPTION_TERMS = [
    (r"\bvery low amount of blood", {("Bleeding", 1)}),
    (r"(?<!very )\blow amount of blood", {("Bleeding", 2)}),
    (r"\bintermediate amount of blood", {("Bleeding", 3)}),
    (r"(?<!very )\bhigh amount of blood", {("Bleeding", 4)}),
    (r"\bvery high amount of blood", {("Bleeding", 5)}),
    (r"\bsuperficial penetration", {("Mechanical injury", 1), ("Thermal injury", 1), ("Mechanical injury", 3),
                                    ("Thermal injury", 3)}),
    (r"\bdeep penetration", {("Thermal injury", 2), ("Mechanical injury", 4), ("Thermal injury", 4)}),
    (r"\bless vital\b", {("Mechanical injury", 1), ("Thermal injury", 1), ("Thermal injury", 2)}),
    (r"(?<!less )\bvital tissue", {("Mechanical injury", 3), ("Thermal injury", 3), ("Mechanical injury", 4),
                                   ("Thermal injury", 4), ("Mechanical injury", 5)}),
    (r"\bfull thickness", {("Mechanical injury", 2)}),
    (r"\bthrough and through", {("Mechanical injury", 5), ("Thermal injury", 5)}),
    (r"\bneedle poke", {("Mechanical injury", 1)}),
    (r"\bindeterminate", {("Ischemic injury", 3)}),
    (r"\bpermanent", {("Ischemic injury", 5)}),
    (r"\binsufficient closure", {("Insufficient closure of anastomosis", 5)}),
]
# The terms an answer must use for each (category, SEVERE grade) it describes.
REQUIRED_TERMS = {
    ("Bleeding", 1): [0], ("Bleeding", 2): [1], ("Bleeding", 3): [2], ("Bleeding", 4): [3], ("Bleeding", 5): [4],
    ("Mechanical injury", 1): [5, 7], ("Mechanical injury", 2): [9], ("Mechanical injury", 3): [5, 8],
    ("Mechanical injury", 4): [6, 8], ("Mechanical injury", 5): [10, 8],
    ("Thermal injury", 1): [5, 7], ("Thermal injury", 2): [6, 7], ("Thermal injury", 3): [5, 8],
    ("Thermal injury", 4): [6, 8], ("Thermal injury", 5): [10],
    ("Ischemic injury", 3): [12], ("Ischemic injury", 5): [13],
    ("Insufficient closure of anastomosis", 5): [14],
}
OUTSIDE = re.compile(
    r"\b(?:vessels?|arter\w*|veins?|spleen|liver|staplers?|clips?|cautery|grasper\w*|tractions?|"
    r"tears?|torn|tearing|perforat\w*|leak\w*|hemorrhag\w*|haemorrhag\w*|transfusion|"
    r"conversion|complications?|outcome|recovery|anatomy|anatomical|tension|pressure|force|"
    r"visibility|view|field)\b", re.I)
OUTCOME = re.compile(
    r"\b(?:controlled|control it|under control|stopped|stop it|fixed|rectified|resolved|managed|handled|"
    r"recovered|contained|addressed it|dealt with|hemostasis|haemostasis|settled)\b", re.I)
SKILL = re.compile(
    r"\b(?:went well|done well|well done|good|great|excellent|poor|careless|careful|carefully|skill\w*|technique\w*|"
    r"competen\w*|impressive|solid|smooth\w*|efficient\w*|clean|cleanly)\b", re.I)
SUGGESTION = re.compile(
    r"\b(?:practi[cs]\w*|drills?|exercises?|try|trying|recommend\w*|suggest\w*|advis\w*|next time|"
    r"in the future|make sure|be careful|slow down|avoid\w*|prevent\w*|should have|could have|"
    r"focus on (?:how|controlling|preventing)|work on)\b", re.I)
CAUSAL = re.compile(r"\b(?:caus\w*|due to|because of|result\w*|led to|leading to|lead to|triggered|provoked|"
                    r"therefore|thus|consequently)\b", re.I)
# Job 41280's single-event answers padded with "That concludes the summary of this phase",
# "for your review" and "you recorded two bleeding events"; the 11-04 review asks for no
# closing line about what was not recorded.
META = re.compile(r"\b(?:conclud\w*|summary|summari[sz]\w*|for your (?:review|records?|reference)|timeline|"
                  r"(?:I|we|you) (?:have )?(?:recorded|documented|noted|logged|observed|noticed|saw)|noting|"
                  r"no other|no further|"
                  r"no additional|nothing else|otherwise uneventful)\b", re.I)
# Each event has one grade from start to end, so job 41334's "reaching severity 3 of 5" implied
# a change the labels do not show.
SEVERITY_CHANGE = re.compile(r"\b(?:reach\w*|r[io]s(?:e|en|ing)|escalat\w*|peak\w*|worsen\w*|climb\w*|"
                             r"increas\w*|grew|grow\w*)\b", re.I)
RAN_PAST = re.compile(r"\b(?:past|beyond|outlast\w*|ran on|runs? on|running on|carried on|continu\w*|"
                      r"into (?:the )?(?:next|following|later)|on through)\b", re.I)
RANKING = re.compile(r"\b(?:most severe|more severe|worst|most serious|more serious|biggest|largest)\b", re.I)
# Job 42234 drafts that passed every other check but read badly: "you had a mechanical injury"
# and "you experienced bleeding" (the injury is the patient's); D1 answers that set D2's priority
# ("you should address ... first", "Then note the bleeding"); the evidence's bookkeeping echoed
# ("ended within that step", "more severe than any in this one"); descriptions pasted in quotes
# or capitals ("a Deep penetration"); and durations to the second ("about thirty-seven seconds").
TRAINEE_AS_PATIENT = re.compile(r"\byou (?:had|have had|experienced?|suffered|sustained|lost|received|incurred)\b", re.I)
PRIORITY = re.compile(r"\b(?:address\w*|attention|priorit\w*|note (?:the|that|this)|focus on)\b", re.I)
ECHO = re.compile(r"\b(?:ended within (?:that|the|this|its) step|within that step|past the end of (?:this|the) phase|"
                  r"more severe than any in this one|described as|recorded together over the same seconds)\b", re.I)
QUOTES = re.compile(r"[\"“”]")
DESCRIPTION_STARTS = {"Very", "Low", "Intermediate", "High", "Superficial", "Deep", "Full-thickness", "Through",
                      "Sign", "All"}
# Minutes and seconds together; "about fourteen seconds" alone cost job 42235 four retries for
# nothing, so a rounded or small figure passes.
EXACT_DURATION = re.compile(r"\b(?:minutes?|a minute) and (?:[a-z]+-)?[a-z]+ seconds?\b", re.I)


def normalized(text: str) -> str:
    text = text.lower().replace("“", "").replace("”", "").replace('"', "").replace("‑", "-")
    return re.sub(r"\s+", " ", text.replace("-", " "))


def described(unit: dict[str, Any]) -> list[tuple[str, int]]:
    return [(e["category"], e["severity"]) for x in unit["incidents"] for e in x["events"]]


def check_answer(answer: str, unit: dict[str, Any], prompt_text: str) -> list[str]:
    problems: list[str] = []
    shown = prompt_text.lower()
    text = normalized(answer)

    def flag(pattern: re.Pattern[str], what: str, licensed: bool = False) -> None:
        hits = [m.group(0) for m in pattern.finditer(answer) if not (licensed and m.group(0).lower() in shown)]
        if hits:
            problems.append(f"{what}: " + ", ".join(f"'{h}'" for h in dict.fromkeys(hits)))

    words = len(answer.split())
    if words > max_words(unit):
        problems.append(f"is too long: {words} words, under {max_words(unit)} needed")
    if "\n" in answer.strip():
        problems.append("is not a single paragraph")
    incidents = unit["incidents"]
    times = {hms(x["start_s"]) for x in incidents} | {hms(x["end_s"]) for x in incidents} | {
        hms(unit["start_s"]), hms(unit["end_s"])}
    if unit["template_id"] == "D5":
        times |= {hms(unit["operation_start_s"]), hms(unit["operation_end_s"])}
    for m in TIME.finditer(answer):
        if m.group(0) not in times:
            problems.append(f"gives a time the events do not have: '{m.group(0)}'")
    allowed = set(re.findall(r"\b\d+\b", TIME.sub(" ", prompt_text)))
    # Durations may be rounded: to whole minutes either way, or under two minutes to 5 or 10 s.
    for x in incidents:
        d = x["end_s"] - x["start_s"] + 1
        allowed |= {str(d // 60), str(d // 60 + 1), str(round(d / 60))}
        if d < 120:
            allowed |= {str(5 * round(d / 5)), str(10 * round(d / 10))}
    stray = [n for n in re.findall(r"\b\d+\b", TIME.sub(" ", answer)) if n not in allowed]
    if stray:
        problems.append("gives a number the evidence does not have: " + ", ".join(f"'{n}'" for n in dict.fromkeys(stray)))
    flag(GRADE_NUMBER, "gives a grade as a number")
    flag(GRADE_TALK_WORDS, "names the grading index, a scale or a grade")
    flag(INDEX_NAME, "names the grading index")
    flag(SEVERITY_WORDS, "says how serious an event was in words the descriptions do not use")
    present = set(described(unit))
    for pattern, licenses in DESCRIPTION_TERMS:
        hit = re.search(pattern, text)
        if hit and not licenses & present:
            problems.append(f"uses the description of a grade no event here has: '{hit.group(0)}'")
    must = present if unit["template_id"] != "D2" else {
        (e["category"], e["severity"]) for e in incidents[unit["focus"]]["events"]}
    for key in sorted(must):
        missing = [DESCRIPTION_TERMS[i][0] for i in REQUIRED_TERMS[key] if not re.search(DESCRIPTION_TERMS[i][0], text)]
        if missing:
            problems.append(f"does not describe the {NOUN[key[0]]} in its description's words, "
                            f"\"{plain(SEVERE_DESCRIPTION[key[0]][key[1]])}\"")
    for category in CATEGORIES:
        if NOUN[category] in text and category not in {c for c, _ in present} and category != "Insufficient closure of anastomosis":
            problems.append(f"mentions {NOUN[category]}, which no event here has")
    flag(OUTSIDE, "adds a cause, structure or consequence the events do not give", licensed=True)
    flag(OUTCOME, "says how an event ended, which the labels do not record")
    flag(SKILL, "judges skill, which the events do not show")
    flag(SUGGESTION, "gives advice, which the events do not support")
    flag(CAUSAL, "links events to causes or results")
    flag(META, "talks about the paragraph, recording, or what was not recorded, not about the events")
    flag(SEVERITY_CHANGE, "implies an event's grade changed, but each event has one grade throughout")
    flag(TRAINEE_AS_PATIENT, "makes the trainee the subject of the patient's event: make the event the subject")
    flag(ECHO, "echoes the evidence's wording instead of saying it as a teacher would")
    flag(EXACT_DURATION, "gives a duration to the second: round it as a speaker would")
    if QUOTES.search(answer):
        problems.append("quotes a description: weave its words into your own sentence, without quotation marks")
    for sentence in re.split(r"(?<=[.!?:;])\s+", answer.strip()):
        capitals = [w.strip(",;:") for w in sentence.split()[1:] if w.strip(",;:\"“") in DESCRIPTION_STARTS]
        if capitals:
            problems.append("capitalizes a description mid-sentence: '" + "', '".join(dict.fromkeys(capitals)) + "'")
    if unit["template_id"] != "D2":
        flag(PRIORITY, "sets a priority or an instruction, which only the address-first question asks for")
    within = {hms(x["start_s"]): x for x in incidents if len(x["step_runs"]) == 1 and len(x["phase_ids"]) == 1}
    ran_on = {hms(x["start_s"]) for x in incidents if hms(x["start_s"]) not in within}
    for sentence in re.split(r"(?<=[.!?])\s+", answer):
        stamps = [m.group(0) for m in TIME.finditer(sentence)]
        if RAN_PAST.search(sentence) and not any(t in ran_on for t in stamps):
            for t in stamps:
                if t in within:
                    problems.append(f"says the event at {t} ran on past its step, but it began and ended during "
                                    f"\"{within[t]['step']}\"")
    # D5's count for a phase with mild events compares it with other operations ("more severe
    # than any in yours"), so ranking words are allowed there.
    ranked = (unit["template_id"] == "D2" or (unit["template_id"] == "D1" and lead_of(incidents) is not None)
              or (unit["template_id"] == "D5" and bool(incidents)))
    if not ranked and RANKING.search(answer):
        problems.append(f"ranks the events ('{RANKING.search(answer).group(0)}'), which the plan does not")
    if unit["template_id"] in ("D1", "D5") and unit["phase"].lower() not in answer.lower():
        problems.append(f"does not name the phase, '{unit['phase']}'")
    starts = [hms(x["start_s"]) for x in incidents]
    if unit["template_id"] == "D5":
        m, total = unit["base_rate"]
        if not (re.search(rf"\b{m}\b", answer) and re.search(rf"\b{total}\b", answer)):
            problems.append(f"does not give why it matters, {m} of the {total} operations with this phase")
        if not incidents and not re.search(r"\b(?:no|without (?:any|an)?)\s*(?:recorded\s+)?adverse events?\b",
                                           answer, re.I):
            problems.append("does not say that no adverse event was recorded in the phase")
        if not re.search(r"\bmatters?\b", answer, re.I):
            problems.append("does not say why it matters (\"That matters because ...\")")
    if unit["template_id"] in ("D1", "D5"):
        for t in starts:
            if t not in answer:
                problems.append(f"does not give the event at {t} its start time")
    if unit["template_id"] == "D2":
        focus = incidents[unit["focus"]]
        for e in focus["events"]:
            if NOUN[e["category"]] not in text:
                problems.append(f"does not name the event to address first, the {NOUN[e['category']]}")
        if focus["step"].lower() not in answer.lower() and hms(focus["start_s"]) not in answer:
            problems.append("does not say where or when that event happened (its step or time)")
        if not re.search(r"\bmost severe\b", answer, re.I):
            problems.append("does not give the reason, that it was the most severe event in the phase")
    return list(dict.fromkeys(problems))


# ---------------------------------------------------------------------------
# Reviewer: a second model call audits every sentence
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """You audit one coaching paragraph against the evidence it was written from.
You do not rewrite it. Report every problem, and nothing else.

Rules:
1. Facts. Every event the paragraph mentions must match EVENTS: its kind, its description's
   words, its step, its time, how long it lasted (a plain-word approximation such as "about
   seven minutes" for 7 min 16 s is fine), and the steps or phase it ran on into. Counts of
   events must be right. Flag a description missing a word that carries the grade (very,
   low, intermediate, high, superficial, deep, less vital, vital, full-thickness, through and
   through), or using another grade's words. Flag saying an event that ended within its step
   ran past it, and saying an event that ran on through other steps happened only during its
   first step.
2. Ranking. Calling an event the most severe, or more severe than another, is allowed only
   where the plan says so. If the plan names an event to address first, the paragraph must
   name that event, and its reason must be that it was the most severe in the phase, with
   its description; how long it lasted and the steps it ran through may support that. Flag
   any other reason.
3. Nothing added. Flag a grade given as a number; naming the grading index, a scale or a
   grade; words for how serious an event was that are not in its description (mild, minor,
   moderate, major, serious, severe, significant, small, large, heavy); causes, anatomy,
   structures, instruments or actions not named in the phase or step names; saying one event
   caused or led to another, including an injury and a bleeding recorded together;
   consequences for the patient or the procedure; how an event ended (stopped, controlled,
   rectified), which is not recorded; judgments of skill; feelings or intentions; sentences
   about the paragraph itself, about anyone recording or reviewing the events, or about what
   was not recorded (rule 5 aside). Flag making the trainee the subject of the patient's
   event ("you had a mechanical injury", "you experienced bleeding"), and, unless the plan
   names an event to address first, telling the trainee to address or note anything.
4. No advice. Flag any suggestion of how to handle, practise, prevent or avoid something, or
   what to do differently. Saying which event to address first, as the plan asks ("You
   should address the bleeding at 0:18:31 first"), answers the question and is not advice.
5. Phase that went best. When the plan names a phase that went best, saying so is a fact,
   and so is saying that no adverse event was recorded in it when EVENTS IN THIS PHASE says
   none. The count under WHY IT MATTERS is its only allowed reason. Flag any other reason, and
   any claim about how the trainee worked in that phase.
Ignore style: rewording that keeps the facts and the descriptions' words is fine.

Return one JSON object and nothing else:
{"problems": [{"quote": "<exact words from the paragraph>", "rule": <1-5>, "why": "<short>"}]}
Use {"problems": []} when there are none."""


def call_llama(host: str, system_prompt: str, prompt: str, temperature: float, max_tokens: int) -> tuple[str, str | None]:
    body = json.dumps({
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": True},
    }).encode("utf-8")
    request = urllib.request.Request(host.rstrip("/") + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=900) as response:
        result = json.loads(response.read().decode("utf-8"))
    message = result["choices"][0]["message"]
    return message.get("content") or "", message.get("reasoning_content")


def json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("reply is not a JSON object")
    return payload


def run_judge(host: str, prompt_text: str, answer: str, max_tokens: int) -> tuple[list[dict[str, Any]], str]:
    last = ""
    for attempt in range(3):
        raw, _ = call_llama(host, JUDGE_SYSTEM, prompt_text + "\n\nPARAGRAPH TO AUDIT\n" + answer,
                            0.0 if attempt == 0 else 0.3, max(max_tokens, 12000))
        try:
            problems = json_object(raw).get("problems")
            if isinstance(problems, list):
                return problems, raw
            last = "no problems list"
        except (ValueError, json.JSONDecodeError) as exc:
            last = str(exc)
    raise ValueError(f"judge reply unreadable: {last}")


def description_clause(e: dict[str, Any], grade: int | None = None) -> str:
    """A description as a clause, for the self-test's known paragraphs."""
    text = plain(SEVERE_DESCRIPTION[e["category"]][grade or e["severity"]])
    if e["category"] == "Bleeding":
        amount = text[0].lower() + text[1:-len(" lost")]
        return ("an " if amount[0] in "aeiou" else "a ") + amount + " was lost"
    if e["category"] == "Insufficient closure of anastomosis":
        return "it was an insufficient closure of the anastomosis"
    return "it was " + ("an " if text[0].lower() in "aeiou" else "a ") + text[0].lower() + text[1:]


def calibration_cases(unit: dict[str, Any]) -> list[tuple[str, bool, str]]:
    """Known paragraphs for one D2 unit: one faithful, four with a planted fault."""
    focus = unit["incidents"][unit["focus"]]
    lead = focus["events"][0]
    noun = " and ".join(NOUN[e["category"]] for e in focus["events"])
    what = "; ".join(description_clause(e) for e in focus["events"])
    # Job 41280's reviewer called "You should address ... first" advice in 13 of 15 D2 answers,
    # so the faithful paragraph answers the question in those words and must pass.
    faithful = (f"You should address the {noun} at {hms(focus['start_s'])}, during {focus['step']}, first. "
                f"It was the most severe of the {len(unit['incidents'])} events in this phase: {what}.")
    others = [g for g in SEVERE_DESCRIPTION[lead["category"]] if g != lead["severity"]]
    wrong = faithful.replace(description_clause(lead),
                             description_clause(lead, min(others)) if others else "a very low amount of blood was lost")
    return [
        ("faithful", False, faithful),
        ("wrong description", True, wrong),
        ("grade as a number", True, faithful + f" It was graded {lead['severity']} of 5."),
        ("invented cause", True, faithful + " It came from a torn vessel."),
        ("advice", True, faithful + " Next time, slow down and keep better traction before you divide tissue."),
    ]


def calibrate(host: str, units: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
    unit = next((u for u in units if u["template_id"] == "D2"), None)
    if unit is None:
        return {"ran": False}
    results = []
    for name, should_flag, paragraph in calibration_cases(unit):
        try:
            problems, _ = run_judge(host, unit["prompt_text"], paragraph, max_tokens)
            results.append({"case": name, "should_flag": should_flag, "flagged": bool(problems),
                            "correct": bool(problems) == should_flag, "paragraph": paragraph, "problems": problems})
        except (ValueError, urllib.error.URLError, TimeoutError) as exc:
            results.append({"case": name, "should_flag": should_flag, "error": str(exc), "correct": False})
    return {"ran": True, "unit": f"{unit['video_id']} {unit['template_id']}",
            "all_correct": all(r["correct"] for r in results), "cases": results}


def rationale(unit: dict[str, Any], source: str, phases: dict[int, str]) -> str:
    def incident(x: dict[str, Any]) -> str:
        found = " with ".join(
            f"{e['category']} SEVERE grade {e['severity']} (label code {e['label_code']}, Event_ID {e['event_id']}, "
            f"from {e['finding_from']})" for e in x["events"])
        steps = " > ".join(f"{r['step_id']} {r['step']} {r['seconds']} s" for r in x["step_runs"])
        ran = (f", running on into phase(s) {x['phase_ids'][1:]}" if len(x["phase_ids"]) > 1 else "")
        return f"{found}, {hms(x['start_s'])}-{hms(x['end_s'])} ({x['end_s'] - x['start_s'] + 1} s), steps {steps}{ran}"
    head = (f"MultiBypass140 IAE labels ({source}), 1 frame per second; phase {unit['phase_id']} {unit['phase']} "
            f"from {hms(unit['start_s'])} to {hms(unit['end_s'])}")
    events = "; ".join(f"[{i + 1}] {incident(x)}" for i, x in enumerate(unit["incidents"])) or "none"
    tail = (" Grade descriptions: SEVERE index, Lavanchy et al. 2025 Table 1. Phase and step names from "
            "tables/phase.csv and tables/step.csv.")
    if unit["template_id"] == "D5":
        m, total = unit["base_rate"]
        common = ", ".join(f"{p} worst grade {w}" for p, w in unit["common_phases"].items())
        rate = (f"{m} of {total} operations with this phase record an adverse event in it" if not unit["incidents"]
                else f"{m} of {total} operations with this phase record an event above grade {unit['worst']} in it")
        return (f"{head}, in one piece; events wholly inside it: {events}. Phases where at least "
                f"{D5_BASE_RATE:.0%} of the series' operations record an event, as worst SEVERE grade in this "
                f"operation (0 = none): {common}. This phase alone has nothing above grade {LOW}, and every other "
                f"has an event above it. Across the series, {rate}.{tail}")
    text = f"{head}; events that began in it: {events}."
    if unit["template_id"] == "D2":
        text += (f" Event {unit['focus'] + 1} is the only one of the {len(unit['incidents'])} at SEVERE grade "
                 f"{HIGH} or above, so it comes first.")
    elif lead_of(unit["incidents"]) is not None:
        lead = lead_of(unit["incidents"])
        high = sum(x["severity"] >= HIGH for x in unit["incidents"]) == 1
        text += (f" Event {lead + 1} is the most severe: " +
                 (f"the only one at SEVERE grade {HIGH} or above." if high else
                  f"every event is {unit['incidents'][lead]['events'][0]['category'].lower()}, and it alone has "
                  "the top grade."))
    return text + tail


def evidence(unit: dict[str, Any]) -> dict[str, Any]:
    """A record's span and event facts, also written by the dry run for verify_mb140_event_evidence.py."""
    def incident(x: dict[str, Any]) -> dict[str, Any]:
        return {"start": hms(x["start_s"]), "end": hms(x["end_s"]), "start_s": x["start_s"], "end_s": x["end_s"],
                "duration_s": x["end_s"] - x["start_s"] + 1,
                "findings": [{"category": e["category"], "severity": e["severity"], "label_code": e["label_code"],
                              "description": e["description"], "event_id": e["event_id"],
                              "finding_from": e["finding_from"]} for e in x["events"]],
                "step": x["step"], "step_id": x["step_id"], "step_runs": x["step_runs"], "phase_ids": x["phase_ids"],
                "began_during": x.get("began_during"), "began_with": x.get("began_with")}
    return {
        "qa_id": f"mb140_{unit['template_id'].lower()}_{unit['video_id']}_{unit['start_s']}_coaching",
        "dataset": "MultiBypass140",
        "video_id": unit["video_id"],
        "center": unit["center"],
        "template_id": unit["template_id"],
        "timestamp": f"{hms(unit['start_s'])}-{hms(unit['end_s'])}",
        "start_s": unit["start_s"],
        "end_s": unit["end_s"],
        "phase": unit["phase"],
        "phase_id": unit["phase_id"],
        "events": [incident(x) for x in unit["incidents"]],
        "focus_event": unit.get("focus"),
        "lead_event": lead_of(unit["incidents"]) if unit["template_id"] == "D1" else None,
        "question": unit["question"],
        **({"question_span": f"{hms(unit['operation_start_s'])}-{hms(unit['operation_end_s'])}",
            "base_rate": {"operations": unit["base_rate"][0], "operations_with_phase": unit["base_rate"][1],
                          "counts": ("an adverse event in the phase" if not unit["incidents"]
                                     else f"an event above grade {unit['worst']} in the phase"),
                          "threshold": D5_BASE_RATE},
            "common_phases_worst_grade": unit["common_phases"]}
           if unit["template_id"] == "D5" else {}),
    }


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iae-root", required=True, type=Path, help="folder holding bern/ and strasbourg/ labels")
    parser.add_argument("--tables", required=True, type=Path, help="MultiBypass140 tables/ with phase.csv, step.csv")
    parser.add_argument("--label-source", default="multibypass06_corrected.zip (CAMMA, 2025-09-02)")
    parser.add_argument("--system-prompt", default="Prompts_And_Pipeline/system-prompt-mb140-coaching-d.md")
    parser.add_argument("--llama-host", default="http://127.0.0.1:18080")
    parser.add_argument("--model-id", default="ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M")
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--max-tokens", type=int, default=10000)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--only", default="",
                        help="comma-separated qa_ids: generate just these units of the selection, e.g. to "
                             "redo answers a faulty check rejected")
    parser.add_argument("--per-template", type=int, default=None,
                        help="questions per template (default: PER_TEMPLATE, 15 each)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    phases, steps = load_names(args.tables)
    videos = load_videos(args.iae_root)
    candidates = build_candidates(videos, phases, steps)
    per_template = ({t: args.per_template for t in PER_TEMPLATE} if args.per_template is not None
                    else dict(PER_TEMPLATE))
    selection = select(candidates, per_template)
    for unit in selection:
        unit["question"], unit["prompt_text"] = build_prompt(unit, phases)
    units = selection
    if args.only:
        wanted = set(args.only.split(","))
        units = [u for u in selection if evidence(u)["qa_id"] in wanted]
        missing = wanted - {evidence(u)["qa_id"] for u in units}
        if missing:
            parser.error(f"--only names units not in the selection: {', '.join(sorted(missing))}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"{len(videos)} videos; candidates D1 {len(candidates['D1'])}, D2 {len(candidates['D2'])}, "
          f"D5 {len(candidates['D5'])}; "
          f"selected {len(selection)}" + (f", generating {len(units)}" if args.only else ""), flush=True)
    if args.dry_run:
        args.output.with_name("prompts_dry_run.txt").write_text(
            "\n\n".join(f"===== {u['video_id']} {u['template_id']} =====\n{u['prompt_text']}\n--- rationale ---\n"
                        f"{rationale(u, args.label_source, phases)}" for u in units) + "\n", encoding="utf-8")
        args.output.with_name("units_dry_run.jsonl").write_text(
            "".join(json.dumps(evidence(u), ensure_ascii=False) + "\n" for u in units), encoding="utf-8")
        print(f"wrote {args.output.with_name('prompts_dry_run.txt')} and units_dry_run.jsonl")
        return 0

    text = Path(args.system_prompt).read_text(encoding="utf-8")
    block = re.search(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    system_prompt = block.group(1).strip() if block else text.strip()

    # The self-test uses the selection's first D2 unit, whichever units --only keeps.
    calibration = calibrate(args.llama_host, selection, args.max_tokens)
    print("audit self-test: " + json.dumps(
        {c["case"]: ("flagged" if c.get("flagged") else "passed") + (" OK" if c["correct"] else " WRONG")
         for c in calibration.get("cases", [])}), flush=True)

    records, slim = [], []
    with args.output.open("w", encoding="utf-8") as handle:
        for index, unit in enumerate(units, 1):
            base = unit["prompt_text"] + '\n\nReturn {"answer": "..."} only.'
            prompt, answer, raw, reasoning, judge_raw, error = base, None, "", None, "", None
            rejected: list[str] = []
            started = time.time()
            attempts = 0
            for attempts in range(1, args.max_retries + 2):
                try:
                    raw, reasoning = call_llama(args.llama_host, system_prompt, prompt, args.temperature, args.max_tokens)
                    candidate = json_object(raw).get("answer")
                    if not isinstance(candidate, str):
                        raise ValueError('reply has no "answer" string')
                    candidate = candidate.strip()
                    problems = check_answer(candidate, unit, unit["prompt_text"])
                    if not problems:
                        audit, judge_raw = run_judge(args.llama_host, unit["prompt_text"], candidate, args.max_tokens)
                        problems = [f"audit rule {p.get('rule')}: \"{p.get('quote', '')}\" ({p.get('why', '')})" for p in audit]
                    if not problems:
                        answer, error = candidate, None
                        break
                    error = "; ".join(problems)
                    rejected.append(error)
                    earlier = [r for r in dict.fromkeys(rejected[:-1]) if not re.match(r"\w*(?:Error|Exception)\b", r)]
                    prompt = (base + "\n\nYOUR PREVIOUS ANSWER WAS REJECTED\n" + f'"{candidate}"\n'
                              + "Problems: " + error + ".\n"
                              + ("Earlier drafts were also rejected for: " + " | ".join(earlier) + ".\n" if earlier else "")
                              + "Rewrite it to fix these problems, keeping to the events above.")
                except (ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    rejected.append(error)
            record = {
                **evidence(unit),
                "answer": answer,
                "rationale": rationale(unit, args.label_source, phases),
                "validation_status": "valid" if answer is not None else "rejected",
                "validation_error": None if answer is not None else error,
                "model": {
                    "backend": "real", "inference_engine": "llama", "model_id": args.model_id,
                    "temperature": args.temperature, "thinking": True, "attempts": attempts,
                    "rejected_attempts_reasons": rejected if answer is not None else rejected[:-1],
                    "system_prompt_path": args.system_prompt, "prompt": prompt, "raw_output": raw,
                    "reasoning_content": reasoning, "audit_raw_output": judge_raw,
                    "elapsed_seconds": round(time.time() - started, 1),
                },
            }
            records.append(record)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            if answer is not None:
                slim.append({k: record[k] for k in ("video_id", "center", "template_id", "timestamp", "start_s",
                                                     "end_s", "phase", "events", "focus_event", "question_span",
                                                     "base_rate", "question", "answer") if k in record})
            print(f"[{index}/{len(units)}] {unit['video_id']} {unit['template_id']} ({unit['phase']}): "
                  f"{record['validation_status']} after {attempts} attempt(s), {record['model']['elapsed_seconds']}s",
                  flush=True)

    args.output.with_name("qa_slim.json").write_text(json.dumps(slim, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {
        "records": len(records),
        "valid": sum(r["validation_status"] == "valid" for r in records),
        "rejected": sum(r["validation_status"] != "valid" for r in records),
        "candidates": {k: len(v) for k, v in candidates.items()},
        "audit_self_test": calibration,
        "label_source": args.label_source,
    }
    args.output.with_name("run_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "audit_self_test"}, indent=2))
    return 0 if summary["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
