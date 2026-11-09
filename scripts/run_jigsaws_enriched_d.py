#!/usr/bin/env python3
"""Ask the written D1, D2 and D5 questions over JIGSAWS trials as a coach would answer them.

One question and answer per trial. Each trial's template comes from --trial-templates and
must pass the Template D ranking strategy (jigsaws_template_d_plan.py). The model sees:

  task       the task's description from the JIGSAWS paper, verbatim
  rubric     the JIGSAWS modified global rating score (Gao et al. 2014, Table 3), verbatim,
             for each technique area the plan covers, with this trial's rating; for Suturing
             and Needle Passing the suture/needle knot wording is restated for the task
  overall    for D1 only, the Overall performance rating and its description, which the
             answer opens with
  why        for D5 only, why the strongest area matters, restated from that area's own
             Table 3 descriptions (Table 3 gives no reasons)
  plan       which areas, in what order, and how long the answer may be
  timing     for a D2 answer about Time and motion rated 3 or lower, in Suturing or Knot
             Tying, where the trial's extra time went, from the gesture transcription, as one
             short sentence. Trial time is the one measure that tracks a rating, Time and motion
             (results/kinematic validation - 10-13-2026, results/area measure validation -
             10-21-2026); no gesture or kinematic measure tracks any other area.

The answer is one short coaching paragraph that names each area the same way ("how you
planned your course of operation"), never names the rating labels, describes a level only
where the rubric does (1, 3 or 5) and then in the description's own words, and makes no
practice or technique suggestion.
Two gates check it: pattern checks against the evidence, then a second model call that
audits every sentence against the rubric levels and timing, and is itself tested on four
known paragraphs at the start of each run. The rationale is written here from the evidence,
and verify_jigsaws_step_evidence.py recomputes every timing number from the raw files.
qa_slim.json keeps only each answer's span, question and answer, for temporal localization.

Dry run (no model; writes the prompts and rationales):
  python3 scripts/run_jigsaws_enriched_d.py --inputs <enriched_trial_inputs.jsonl> \\
      --output out/qa_records.jsonl --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_jigsaws_enriched_pilot import size_words  # noqa: E402
from jigsaws_paper_reference import (  # noqa: E402
    GESTURES,
    GRS_RUBRIC,
    SUTURE_NEEDLE_ADAPTED,
    TASK_DESCRIPTIONS,
    rubric_for,
    why_it_matters,
)
from jigsaws_template_d_plan import (  # noqa: E402
    D_QUESTIONS,
    TECHNIQUE_AREAS,
    frame_to_timestamp,
    join_labels,
    template_plan,
)

# One question per trial, each allowed by the ranking strategy (jigsaws_template_d_plan.py).
# D5 needs one area alone at 5, which in JIGSAWS is only flow of operation in Suturing:
# Suturing_C001 and Suturing_F002. Knot_Tying_C001, with respect for tissue alone at 4, was
# dropped because the rubric does not describe a 4 (the user, 2026-10-21). D1 opens with the
# Overall performance verdict, so its trials sit at the levels the rubric describes: 5
# (Suturing_F003), 3 (Suturing_B002) and 1 (Needle_Passing_C003). D2 keeps its trials.
DEFAULT_TRIAL_TEMPLATES = (
    "Suturing_C001:D5,Suturing_F002:D5,"
    "Suturing_B001:D2,Knot_Tying_D003:D2,Needle_Passing_F003:D2,"
    "Suturing_F003:D1,Suturing_B002:D1,Needle_Passing_C003:D1"
)

RECENT_OPENERS_TO_SHOW = 5

# Trial time against the Time and motion rating (Spearman, 5,000 permutations), from
# results/kinematic validation - 10-13-2026; the partial correlation, with the other
# three technique ratings held fixed, is from results/area measure validation -
# 10-21-2026, which also found no gesture or kinematic measure that tracks any other
# area. Timing is used only where it held up.
TIME_VALIDATION = {
    "Suturing": {"rho": -0.49, "p": "0.002", "n": 39, "partial_rho": -0.43, "partial_p": "0.006", "validated": True},
    "Knot_Tying": {"rho": -0.81, "p": "<0.001", "n": 36, "partial_rho": -0.45, "partial_p": "0.007", "validated": True},
    "Needle_Passing": {"rho": -0.14, "p": "0.46", "n": 28, "partial_rho": 0.10, "partial_p": "0.62", "validated": False},
}
MATERIAL_TIME_RATIO = 1.25
# One short sentence with at most two steps: the user found the three-step sentence with
# counts long and confusing (2026-10-21).
MAX_TIME_STEPS = 2
TIME_REFERENCE = "the runs judged most efficient on this task"

# How the answer names each area. "How you planned your course of operation" is the
# user's choice for flow of operation (2026-10-21); the others turn the rubric's own words
# ("tissue handling", "suture control", "efficient") around so no rating label is spoken.
AREA_NAMES = {
    "respect_for_tissue": "the way you handled the tissue",
    "suture_needle_handling": "your control of the needle and suture",
    "time_and_motion": "how efficiently you moved",
    "flow_of_operation": "how you planned your course of operation",
}
AREA_NAME_PATTERNS = {
    "respect_for_tissue": r"\b(?:the way|how) you handled the tissue\b|\bhandling of the tissue\b",
    "suture_needle_handling": r"\bcontrol of the (?:needle and suture|suture and needle)\b",
    "time_and_motion": r"\bhow efficiently you (?:moved|move)\b",
    "flow_of_operation": r"\bplann\w*\b[^.]{0,30}\bcourse of (?:the )?operation\b",
}
NAME_NOTES = {
    "respect_for_tissue": "; where its descriptions say 'tissue handling', say 'handling of the tissue'",
    "time_and_motion": "; where its descriptions say 'time/motion', say 'movement'",
}
# The earlier names for flow of operation, which the user found opaque.
OLD_FLOW_NAMES = re.compile(
    r"\b(?:the way you moved from one step to the next|how the procedure flowed|"
    r"how clearly you planned your way through)\b",
    re.IGNORECASE,
)
LABEL_HINT = (
    "Instead of a label, name the area as: 'the way you handled the tissue' (and 'careful handling "
    "of the tissue' where a description says 'careful tissue handling'); 'your control of the needle "
    "and suture'; 'how efficiently you moved' (never 'time and motion' or 'time/motion'); 'how you "
    "planned your course of operation'."
)

# Table 2's gestures as spoken clauses, for the timing evidence.
GESTURE_PHRASES = {
    "G1": "reaching for the needle with your right hand",
    "G2": "positioning the needle",
    "G3": "pushing the needle through the tissue",
    "G4": "transferring the needle from your left hand to your right",
    "G5": "moving to the center with the needle in grip",
    "G6": "pulling the suture with your left hand",
    "G7": "pulling the suture with your right hand",
    "G8": "orienting the needle",
    "G9": "using your right hand to help tighten the suture",
    "G10": "loosening more suture",
    "G11": "dropping the suture at the end and moving to the end points",
    "G12": "reaching for the needle with your left hand",
    "G13": "making the C loop around your right hand",
    "G14": "reaching for the suture with your right hand",
    "G15": "pulling the suture with both hands",
}
GESTURE_MENTION = {
    "G1": r"reach\w*\s+for\s+the\s+needle", "G2": r"position\w*\s+the\s+needle",
    "G3": r"push\w*\s+the\s+needle", "G4": r"transfer\w*\s+the\s+needle|from your left hand to your right",
    "G5": r"mov\w*\s+to\s+the\s+cent", "G6": r"pull\w*\s+the\s+suture",
    "G7": r"pull\w*\s+the\s+suture", "G8": r"orient\w*\s+the\s+needle",
    "G9": r"help\w*\s+tighten\s+the\s+suture", "G10": r"loosen\w*\s+more\s+suture",
    "G11": r"end points|drop\w*\s+the\s+suture",
    "G12": r"reach\w*\s+for\s+the\s+needle", "G13": r"\bC loop\b",
    "G14": r"reach\w*\s+for\s+the\s+suture", "G15": r"pull\w*\s+the\s+suture",
}
def placement_levels(score: int) -> set[int]:
    return {score} if score in (1, 3, 5) else {score - 1, score + 1}


def next_level(score: int) -> int | None:
    return {1: 3, 2: 3, 3: 5, 4: 5}.get(score)


# Wording only one described level of one element uses; an answer may not use a
# level's wording where the trial neither sits at or next to it nor is reaching for it.
ANCHOR_PHRASES = {
    "respect_for_tissue": {1: r"unnecessary force", 3: r"occasional\w*[^.]{0,30}inadvertent damage|inadvertent damage[^.]{0,15}occasional", 5: r"consistent(?:ly)?,? appropriate"},
    "time_and_motion": {3: r"some unnecessary moves", 5: r"clear economy|maximum efficiency"},
    "flow_of_operation": {1: r"frequently interrupted|discuss the next move",
                          3: r"forward planning|reasonable procedure progression",
                          5: r"obviously planned"},
}
NEEDLE_ANCHOR_PHRASES = {
    "Knot_Tying": {1: r"entangle\w*|poor knot tying", 3: r"majority of knots", 5: r"excellent suture control"},
    "Suturing": {1: r"entangle\w*|poor suturing", 3: r"majority of sutures", 5: r"excellent suture control"},
    "Needle_Passing": {1: r"entangle\w*|poor needle passing", 3: r"majority of needle passes", 5: r"excellent suture control"},
}


def anchor_phrases(area: str, task_key: str) -> dict[int, str]:
    return NEEDLE_ANCHOR_PHRASES[task_key] if area == "suture_needle_handling" else ANCHOR_PHRASES[area]


# ---------------------------------------------------------------------------
# Timing evidence
# ---------------------------------------------------------------------------


def time_findings(record: dict[str, Any], task_key: str, plan: dict[str, Any], scores: dict[str, int]) -> dict[str, Any] | None:
    """The timing an answer may use, or None where it does not apply or is not validated."""
    evidence = record["gesture_level_evidence"].get("time_evidence")
    if not evidence or not TIME_VALIDATION[task_key]["validated"]:
        return None
    if plan["case"] == "overall" or "time_and_motion" not in plan["areas"] or scores["time_and_motion"] > 3:
        return None
    if evidence["ratio"] < MATERIAL_TIME_RATIO:
        return None
    extra_total = evidence["trial_seconds"] - evidence["reference_median_trial_seconds"]
    candidates = [
        g for g in evidence["by_gesture"]
        if g["extra_seconds"] >= max(5.0, 0.1 * extra_total)
        and g["total_seconds"] >= 1.5 * g["reference_median_total_seconds"]
    ]
    steps = sorted(candidates, key=lambda g: -g["extra_seconds"])[:MAX_TIME_STEPS]
    share = sum(g["extra_seconds"] for g in steps) / extra_total if steps else 0.0
    lead = "most of" if share >= 0.5 else "much of" if share >= 0.3 else "some of"
    size, comparator = size_words(evidence["ratio"], "long").rsplit(" ", 1)
    sentence = f"Your run took {size} {comparator} {TIME_REFERENCE}"
    if steps:
        sentence += f", and {lead} the extra time went into " + join_labels([GESTURE_PHRASES[g["gesture_id"]] for g in steps])
    lines = [sentence + "."]
    return {"lines": lines, "steps": steps, "evidence": evidence, "share": round(share, 3)}


# ---------------------------------------------------------------------------
# Answer checks. Each returns a list of problems; an empty list keeps the answer.
# ---------------------------------------------------------------------------

DIGIT = re.compile(r"\d")
GESTURE_CODE = re.compile(r"\bG(?:1[0-5]|[1-9])\b")
META_TERM = re.compile(
    r"\b(?:scores?|scored|scoring|ratings?|rated|rubrics?|anchors?|scales?|GRS|OSATS|"
    r"subscores?|annotat\w*|data|dataset|measurements?|kinematic\w*|sensors?|"
    r"gestures?|frames?|timestamps?|reference|median|ratios?|percent\w*|evidence|"
    r"descriptions?|assessments?|metrics?|standards?)\b",
    re.IGNORECASE,
)
LABELS = re.compile(
    r"\b(?:tissue handling|respect for tissue|instrument/needle handling|instrument and needle "
    r"handling|needle handling|suture/needle handling|suture and needle handling|economy of "
    r"motion|time and motion|time/motion|flow of operation|overall performance|quality of "
    r"(?:the )?final product)\b",
    re.IGNORECASE,
)
# No level is placed against the ones above or below it (the user's rule of 2026-10-21):
# "beyond X, short of Y", "not yet", "the next level" all compare a rating with others.
COMPARISON = re.compile(
    r"\b(?:beyond|short of|not yet|fell short|falls short|falling short|went past|moved past|"
    r"gone past|past the point|surpass\w*|exceed\w*|level above|level below|next level|higher level|"
    r"lower level|step up|moving up|move up|between (?:the )?(?:two|levels?|descriptions?))\b",
    re.IGNORECASE,
)
HEADING = re.compile(r"(?:^|\n|[.!?]\s+)[A-Z][A-Za-z/]*(?: [A-Za-z/]+){0,2}:\s")
EXPERIENCE = re.compile(r"\b(?:novice|expert|beginner|experienced|hours of)\b", re.IGNORECASE)
EXCLUDED_AREA = re.compile(r"\b(?:final product|finished (?:work|product)|end result)\b", re.IGNORECASE)
NOT_BENCH_TOP = re.compile(
    r"\b(?:patients?|anatomy|anatomical|bleed\w*|heal\w*|assistant|operating room|"
    r"wound|fascia|arter\w*|veins?|organs?|perfusion|operative field)\b",
    re.IGNORECASE,
)
# Mechanics, consequences and inner states no evidence ever supplies. A word is allowed
# where the task, rubric or timing text shown for this record uses it.
OUTSIDE_KNOWLEDGE = re.compile(
    r"\b(?:wrists?|tremors?|shak\w*|jerk\w*|wobbl\w*|drift\w*|wander\w*|sweep\w*|"
    r"arcs?|detours?|angles?|non-?dominant|postures?|ergonom\w*|elbows?|shoulders?|"
    r"fingers?|fatigue\w*|tired|tiring|stamina|energy|confiden\w*|re-?grasp\w*|"
    r"over-?adjust\w*|over-?correct\w*|fumbl\w*|slip\w*|tear\w*|search\w*|"
    r"grips?|gripping|grasp\w*|tension|risk\w*|safe|safety|safely|prevent\w*|"
    r"excessive|instinct\w*|stable|stability|nerv\w*|anxi\w*|stress\w*|appreciat\w*|effort|"
    r"hard work|dedicat\w*|commitment|proud)\b",
    re.IGNORECASE,
)
SUGGESTION = re.compile(
    r"\b(?:practi[cs]\w*|drills?|exercises?|try|trying|recommend\w*|suggest\w*|advis\w*|"
    r"next time|in the future|start by|begin by|slow down|speed up|take your time|"
    r"picture|visuali[sz]\w*|imagine)\b",
    re.IGNORECASE,
)
# Only the formal connectors and the known "naturally" pattern are blocked here; whether an
# everyday "so that" or "leads to" adds a consequence is the audit's call (rule 4), since
# blocking them outright stalled v6 on wording the evidence allowed.
CAUSAL_LINK = re.compile(
    r"\b(?:therefore|thus|consequently|as a result|will naturally|naturally (?:improve|follow)\w*)\b",
    re.IGNORECASE,
)
THROUGHOUT = re.compile(r"\b(?:throughout|every step|from start to finish|constantly|all the time)\b", re.IGNORECASE)
# One run has no before and after.
CHANGE_OVER_TIME = re.compile(
    r"\bimprov(?:ement|ed|ing)\s+(?:over|from|on)\b|\b(?:has|have)\s+(?:improved|progressed|grown)\b|"
    r"\bprogress(?:ed|ing)?\s+(?:from|beyond)\b|\bstarting to\b|\bbecoming\b|\bcompared (?:to|with) before\b|"
    r"\b(?:just yet|yet to|as yet|so far|right now|for now|at this (?:stage|point))\b|\byet(?=\s*[.,;:!?])",
    re.IGNORECASE,
)
SCALE_TALK = re.compile(r"\btiers?\b|\b(?:lower|higher|highest|top|bottom)\s+(?:levels?|tiers?|end of the scale)\b", re.IGNORECASE)
OVERCLAIM = re.compile(
    r"\b(?:without deviation|flawless\w*|perfect\w*|understand\w*|talent\w*|effortless\w*|"
    r"master\w*|grasp of|unbroken|uninterrupted|seamless\w*|without (?:any )?(?:interruptions?|hesitation|pauses?)|"
    r"without breaking|rhythm\w*|smooth\w*)\b",
    re.IGNORECASE,
)
STEP_LANGUAGE = re.compile(
    r"\b(?:travel\w*|farther|further|distance|took (?:longer|about|somewhat|more)|"
    r"as long as|longer than|extra time|time went|stationary|speed|"
    r"faster|quicker|slower|slowly|seconds?|minutes?)\b",
    re.IGNORECASE,
)
WHICH_TIME = re.compile(
    r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last|final)\s+"
    r"(?:time|pass|pull|reach)\b",
    re.IGNORECASE,
)
SIZE_WORDS = re.compile(
    r"\b(?:twice|(?:one and a half|two|three|four|five|six|seven|eight|several|many) times|"
    r"double\w*|triple\w*|half again)\b",
    re.IGNORECASE,
)
# A goal the trainee is still to reach is not something to keep up.
KEEP_GOAL = re.compile(r"\bgoal (?:here |there |now )?is (?:to )?(?:maintain|keep|continu|sustain)\w*", re.IGNORECASE)
HAND = re.compile(r"\b(?:left|right)(?=[- ](?:hand|instrument|arm|side|tool))", re.IGNORECASE)
KNOT = re.compile(r"\bknots?\b|\bknot[- ]tying\b", re.IGNORECASE)
# Short answers (the user's request of 2026-10-21): D2 as short as the Knot_Tying_D003 answer
# the user held up, D1 an overall verdict and the areas that stood out.
MAX_WORDS = {"D1": 50, "D2": 95, "D5": 85}
# D5's reason must come from the area's rubric, not from where the area ranks.
CIRCULAR_WHY = re.compile(
    r"\bmatters?\b[^.]*\b(?:stands? (?:out|above)|sets? (?:it|this|this area|this skill) apart|"
    r"(?:the|your) other (?:three )?areas|strongest|weaker)\b",
    re.IGNORECASE,
)
# Any form of the verdict word: "competent", "competently", "competence".
OVERALL_WORDS = {1: r"\bvery poor\w*", 3: r"\bcompeten\w*", 5: r"\bsuperior\w*"}
VAGUE_REFERENCE = re.compile(r"\bthose runs\b", re.IGNORECASE)
AREA_STEMS = {
    "respect_for_tissue": re.compile(r"\b(?:tissue|tube|material|pad)\b", re.IGNORECASE),
    "suture_needle_handling": re.compile(r"\b(?:needles?|sutures?|suturing|knots?|entangle\w*)\b", re.IGNORECASE),
    "time_and_motion": re.compile(r"\b(?:econom\w*|efficien\w*|motion|movements?|moves?|moved|moving|wasted)\b", re.IGNORECASE),
    "flow_of_operation": re.compile(
        r"\b(?:flow\w*|plan\w*|progress\w*|transitions?|interrupt\w*|course|sequence|"
        r"one step to the next|step to step)\b",
        re.IGNORECASE,
    ),
}


def check_answer(answer: str, unit: dict[str, Any], earlier_openers: list[str] = ()) -> list[str]:
    """Problems with one answer, judged against what its prompt carried."""
    problems: list[str] = []
    shown = unit["shown_text"].lower()
    timing = unit["timing"]
    timing_text = "\n".join(timing["lines"]).lower() if timing else ""
    task_key, scores, plan = unit["task_key"], unit["scores"], unit["plan"]

    def flag(pattern: re.Pattern[str], what: str) -> None:
        hits = list(dict.fromkeys(m.group(0) for m in pattern.finditer(answer)))
        if hits:
            problems.append(f"{what}: " + ", ".join(f"'{h}'" for h in hits))

    if len(answer) < 80 and unit["template_id"] != "D1":
        problems.append("answer is too short to answer the question")
    words = len(answer.split())
    if words > MAX_WORDS[unit["template_id"]]:
        problems.append(f"is too long: {words} words, where this question needs under {MAX_WORDS[unit['template_id']]}")
    first = re.match(r"(.+?[.!?])(?:\s|$)", answer)
    first = first.group(1) if first else answer
    if unit["template_id"] in ("D2", "D5"):
        area = plan["areas"][0]
        if not re.search(AREA_NAME_PATTERNS[area], first, re.IGNORECASE):
            problems.append(f"does not name the area in its first sentence as '{AREA_NAMES[area]}'")
    if unit["template_id"] == "D1":
        overall = scores["overall_performance"]
        if not re.search(OVERALL_WORDS[overall], first, re.IGNORECASE):
            problems.append("does not open with the overall verdict the plan asks for: "
                            f"'{GRS_RUBRIC['overall_performance'][overall]}'")
    if unit["template_id"] == "D5":
        if not re.search(r"\bmatter", answer, re.IGNORECASE):
            problems.append("does not say why the skill matters")
        flag(CIRCULAR_WHY, "explains why it matters by where the area ranks, which is circular; use WHY IT MATTERS")
    # Answers in one batch open differently: three D2s in job 35885 all began "The other three
    # areas are stronger, but", although each saw the openings already used.
    opening = " ".join(re.findall(r"[a-z]+", answer.lower())[:4])
    if any(" ".join(re.findall(r"[a-z]+", o.lower())[:4]) == opening for o in earlier_openers):
        problems.append(f"opens the same way as an earlier answer in this batch ('{opening}'); open differently")
    flag(OLD_FLOW_NAMES, "names the planning of the course of operation in other words; "
                         "say 'how you planned your course of operation'")
    flag(VAGUE_REFERENCE, "says 'those runs' without saying which runs; say 'the runs judged most efficient on this task'")
    flag(DIGIT, "contains a number")
    flag(GESTURE_CODE, "names a gesture code")
    flag(META_TERM, "talks about the rating system instead of the performance")
    flag(LABELS, "names a rating label; describe the area in plain words instead")
    flag(EXPERIENCE, "mentions the trainee's experience level, which was not given")
    flag(EXCLUDED_AREA, "mentions the final product, which the question does not ask about")
    flag(NOT_BENCH_TOP, "mentions something a bench-top trial does not have")
    flag(SUGGESTION, "makes a practice or technique suggestion, which is not permitted")
    flag(THROUGHOUT, "stretches a whole-trial rating over every moment")
    flag(OVERCLAIM, "claims more than the rubric says")
    flag(CHANGE_OVER_TIME, "describes change over time, but this is one run with no before and after")
    flag(SCALE_TALK, "talks about the rating scale's tiers instead of the performance")
    if "\n" in answer.strip() or HEADING.search(answer):
        problems.append("is not a single flowing paragraph (line breaks or 'Label:' headings)")
    flag(COMPARISON, "compares this level with the levels above or below, which is not permitted")
    causal = []
    for m in CAUSAL_LINK.finditer(answer):
        before = re.findall(r"[a-z]+", answer[:m.start()].lower())[-1:]
        if not (before and f"{before[0]} {m.group(0).lower()}" in shown):
            causal.append(m.group(0))
    if causal:
        problems.append("links areas causally: " + ", ".join(f"'{c}'" for c in dict.fromkeys(causal)))
    outside = [m.group(0) for m in OUTSIDE_KNOWLEDGE.finditer(answer)
               if m.group(0).lower() not in shown and m.group(0).lower() not in timing_text]
    if outside:
        problems.append("uses knowledge from outside the rubric: " + ", ".join(f"'{w}'" for w in dict.fromkeys(outside)))
    if task_key != "Knot_Tying":
        flag(KNOT, "mentions knots in a task that ties none")
    if unit["goal_areas"] and not (unit["template_id"] == "D1" and 5 in scores.values()):
        flag(KEEP_GOAL, "states the goal as something to keep up, which the rating does not support")

    if timing:
        if not any(re.search(GESTURE_MENTION[g["gesture_id"]], answer, re.IGNORECASE) for g in timing["steps"]) and timing["steps"]:
            problems.append("does not say where the extra time went, which the plan asks for")
        allowed_gestures = {g["gesture_id"] for g in timing["steps"]}
        for gid, pattern in GESTURE_MENTION.items():
            m = re.search(pattern, answer, re.IGNORECASE)
            if m and gid not in allowed_gestures and not any(
                re.search(GESTURE_MENTION[a], m.group(0), re.IGNORECASE) for a in allowed_gestures
            ):
                problems.append(f"names a step the timing does not list: '{m.group(0)}'")
    else:
        flag(STEP_LANGUAGE, "describes timing or movement the prompt did not give")
        for gid, pattern in GESTURE_MENTION.items():
            m = re.search(pattern, answer, re.IGNORECASE)
            if m:
                problems.append(f"places a quality at a particular step: '{m.group(0)}'")
                break
    for pattern, what in ((WHICH_TIME, "names a particular pass the evidence does not give"),
                          (SIZE_WORDS, "states a size or count the evidence does not give"),
                          (HAND, "names a hand the evidence does not give")):
        bad = [m.group(0) for m in pattern.finditer(answer) if m.group(0).lower() not in timing_text]
        if bad:
            problems.append(f"{what}: " + ", ".join(f"'{b}'" for b in dict.fromkeys(bad)))

    for area in plan["areas"]:
        label = AREA_NAMES[area]
        if unit["template_id"] != "D1" and not AREA_STEMS[area].search(answer):
            problems.append(f"never addresses {label}, which the plan requires")
        allowed = {scores[area]} if scores[area] in (1, 3, 5) else set()
        if unit["template_id"] == "D1":
            # Overall feedback mentions only what stood out (4 or 5), never an area below 4.
            allowed &= {5}
            if scores[area] < 4 and re.search(AREA_NAME_PATTERNS[area], answer, re.IGNORECASE):
                problems.append(f"mentions {label}, rated below 4; overall feedback mentions only what stood out")
        if area in unit["goal_areas"] and next_level(scores[area]):
            allowed.add(next_level(scores[area]))
        must = set()
        if unit["template_id"] in ("D2", "D5") and scores[area] in (1, 3, 5):
            must.add(scores[area])
        if unit["template_id"] == "D1" and scores[area] == 5:
            must.add(5)
        if area in unit["goal_areas"] and next_level(scores[area]):
            must.add(next_level(scores[area]))
        for level in must:
            pattern = anchor_phrases(area, task_key).get(level)
            if pattern and not re.search(pattern, answer, re.IGNORECASE):
                problems.append(f"does not use the level {level} description's own words for {label}: "
                                f"{rubric_for(area, task_key)[level]!r}")
        if unit["why"]:
            allowed |= {level for level, pattern in anchor_phrases(area, task_key).items()
                        if re.search(pattern, unit["why"], re.IGNORECASE)}
        for level, pattern in anchor_phrases(area, task_key).items():
            m = re.search(pattern, answer, re.IGNORECASE)
            if m and level not in allowed:
                problems.append(
                    f"describes {label} with the {level} description's wording ('{m.group(0)}'), "
                    "which is neither this trial's described level nor its goal"
                )
    return list(dict.fromkeys(problems))


# ---------------------------------------------------------------------------
# Second gate: a model audit of every sentence against the evidence
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """You audit one coaching paragraph against the evidence it was written from.
You do not rewrite it. Report every problem, and nothing else.

Rules:
1. Level. The rubric describes only 1, 3 and 5. For an area rated 1, 3 or 5, any statement
   about how it went must match that description in meaning and degree. For an area rated
   2 or 4, the paragraph must say nothing about how that area went: no quality, no
   description, no comparison with the descriptions above or below. Naming it, saying how
   it ranks against the other areas, and stating its goal are fine. The area names in the
   RUBRIC headings ("how efficiently you moved", "how you planned your course of
   operation") are names, not descriptions: "how efficiently you moved was also one of
   your strengths" only names and ranks the area. Flag any statement that breaks this
   rule, or that makes an area sound better or worse than its description.
2. Goals. Where the plan asks for a goal, the paragraph must state it faithfully and
   plainly. Flag it if missing or wrong. Flag any comparison of the trial's level with
   the levels above or below ("beyond", "short of", "not yet", "the next level"). A goal
   for an area rated below 5 is wrong if stated as something to maintain or keep. Where
   the plan asks for no goal, none is needed.
3. Timing. Statements about time, particular steps or how often something was done must
   match WHERE THE TIME WENT exactly: the same steps and sizes. If that section is
   absent, the paragraph must not mention timing or particular steps at all. Words that
   only place the paragraph in this run ("during the run", "in this run") are not timing,
   and the rubric's own words ("poor needle passing", "needle passes") are never a step.
4. Nothing added. Flag technique, mechanics, grips, hand or instrument positions, causes,
   consequences, feelings, intentions or mental states ("thoughtful", "dedicated"), praise
   for effort or focus, claims about understanding, improvement or change over time (there
   is one run, with no before), talk about the rating scale itself (its tiers, how it works,
   what is "expected at the highest tier"), comparisons with other trainees, and any
   particular action or step (reaches, pulls, passes, loops) not listed under WHERE THE
   TIME WENT. None of these is in the evidence.
5. No suggestions. Flag practice, drills, exercises, technique advice, and mental
   techniques such as picturing or visualizing.
6. Overall and why. When the evidence has OVERALL, the paragraph's overall verdict must
   match its description in meaning and degree. When it has WHY IT MATTERS, the
   paragraph's reason why the area matters must match that reason in meaning; that
   reason is evidence, not an added consequence under rule 4. A reason resting on where
   the area ranks ("it matters because it stands above your other areas") is circular:
   flag it.
Stating a goal plainly ("the goal is...") is required by rule 2 and is never a problem under
rules 4 or 5.
Ignore style: rewording that keeps the meaning is fine, and the paragraph should not name
the rating labels.

Return one JSON object and nothing else:
{"problems": [{"quote": "<exact words from the paragraph>", "rule": <1-6>, "why": "<short>"}]}
Use {"problems": []} when there are none."""


def judge_prompt(unit: dict[str, Any], answer: str) -> str:
    return "\n".join([unit["prompt_body"], "", "PARAGRAPH TO AUDIT", answer])


def parse_judge(text: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    payload = json.loads(cleaned[start:end + 1])
    problems = payload.get("problems")
    if not isinstance(problems, list):
        raise ValueError("judge reply has no problems list")
    return problems


def run_judge(host: str, unit: dict[str, Any], answer: str, max_tokens: int) -> tuple[list[dict[str, Any]], str]:
    last_error = ""
    for attempt in range(3):
        raw, _ = call_llama(host, JUDGE_SYSTEM, judge_prompt(unit, answer), 0.0 if attempt == 0 else 0.3,
                            max(max_tokens, 12000))
        try:
            return parse_judge(raw), raw
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
    raise ValueError(f"judge reply unreadable: {last_error}")


# Paragraphs with a known verdict, run through the judge before any record is trusted
# to it. All are judged against Needle_Passing_C003's D1 evidence (overall 1; tissue 1,
# suture/needle 1, time and motion 1, flow 2; no timing evidence). Flow at 2 has no rubric
# description, so a faithful paragraph leaves it out.
CALIBRATION_UNIT = ("Needle_Passing_C003", "D1")
FAITHFUL = "Your run was very poor, and no area of your technique stood out."
CALIBRATION = [
    ("faithful", False, FAITHFUL),
    ("overstated", True, "Your run was competent, and your needle passing was excellent."),
    ("describes a 2", True, FAITHFUL + " Your course of operation showed some forward planning, though "
                                       "your transitions between moves were sometimes slow."),
    ("invented", True,
     "Your immediate focus needs to be on how you handle the suture and how efficiently you move your "
     "instruments. Developing a secure grip and smooth transitions will prevent fumbling and maintain "
     "your rhythm, and streamlining your movements reduces fatigue. Practice bringing your instruments "
     "directly to the task without extra adjustments, and let your non-dominant hand support the "
     "needle driver rather than working against it."),
]


def calibrate_judge(host: str, units: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
    trial_id, template_id = CALIBRATION_UNIT
    unit = next((u for u in units if u["trial_id"] == trial_id and u["template_id"] == template_id), None)
    if unit is None:
        return {"ran": False, "reason": f"{trial_id} {template_id} not in this run"}
    results = []
    for name, should_flag, paragraph in CALIBRATION:
        try:
            problems, _ = run_judge(host, unit, paragraph, max_tokens)
            flagged = bool(problems)
            results.append({"case": name, "should_flag": should_flag, "flagged": flagged,
                            "correct": flagged == should_flag, "problems": problems})
        except (ValueError, urllib.error.URLError, TimeoutError) as exc:
            results.append({"case": name, "should_flag": should_flag, "error": str(exc), "correct": False})
    return {"ran": True, "all_correct": all(r["correct"] for r in results), "cases": results}


# ---------------------------------------------------------------------------
# Prompt and rationale
# ---------------------------------------------------------------------------


def rubric_lines(area: str, score: int, task_key: str, wants_goal: bool, name_it: bool = True) -> list[str]:
    """One area's rubric, its rating, and whether the answer may describe it.

    The rubric describes only 1, 3 and 5. A trial at one of those is described in
    the answer; a 2 or 4 is not described at all (the user's rule of 2026-10-21), and
    no level is placed against the ones above or below it.
    """
    anchors = rubric_for(area, task_key)
    # The element's own name ("Time and motion") stays out: it is a rating label the
    # answer must not echo. The record keeps it under evidence.rubric_shown.
    name = AREA_NAMES[area]
    lines = [f"{name[0].upper() + name[1:]} (in the answer, name it \"{name}\"{NAME_NOTES.get(area, '')})"
             if name_it else
             f"{name[0].upper() + name[1:]} (do not name this area in the answer{NAME_NOTES.get(area, '')})"]
    lines += [f"  {level}: {anchors[level]}" for level in (1, 3, 5)]
    if score in (1, 3, 5):
        lines.append(f"  This trial: {score}. The rubric describes this level: say what it looked like, "
                     "in the description's own words, not paraphrased.")
    else:
        lines.append(f"  This trial: {score}. The rubric does not describe this level, so say nothing "
                     "about how this area went.")
    goal = next_level(score)
    if wants_goal and goal:
        lines.append(f"  Goal for this area: the {goal} description, stated plainly as the goal (never as "
                     "something to maintain or keep), with no comparison to this trial's level.")
    return lines


def goal_areas(template_id: str, plan: dict[str, Any]) -> list[str]:
    """The areas whose answer states a goal: only D2's priority area.

    D1 is an overall verdict and the areas that stood out, and D5 gives the reason the
    skill matters; a goal for each area is what made D1 long (the user, 2026-10-21).
    """
    return list(plan["areas"]) if template_id == "D2" else []


def plan_lines(template_id: str, plan: dict[str, Any], scores: dict[str, int], has_timing: bool) -> list[str]:
    no_compare = "Never compare a level with the ones above or below it."
    case = plan["case"]
    if case == "overall":
        # The verdict, then only what stood out: areas at 4 or 5 (the user, 2026-10-21).
        # An area below 4 is not worth mentioning, and a 4 is named without description.
        top5 = [AREA_NAMES[a] for a in TECHNIQUE_AREAS if scores[a] == 5]
        top4 = [AREA_NAMES[a] for a in TECHNIQUE_AREAS if scores[a] == 4]
        if top5 or top4:
            what = "Then say what stood out, in one sentence:"
            if top5:
                what += f" {join_labels(top5)}, each with its level 5 description in its own words;"
            if top4:
                what += f" {join_labels(top4)}, named only, as it has no description;"
            what = what.rstrip(";") + "."
        else:
            what = "Then say in a few words that no area stood out, naming none."
        return [
            "Write overall feedback in one or two short sentences, under 50 words, like a teacher's "
            "verdict on the whole run.",
            "First sentence: the overall verdict from OVERALL.",
            what,
            "Say nothing at all about an area rated below 4. No goals, no timing, and no practice or "
            f"technique suggestions. {no_compare}",
        ]
    area = plan["areas"][0]
    name = AREA_NAMES[area]
    if case == "focus":
        why = ("Why: it is the weakest of the four; say what it looked like in its description's own words, not paraphrased."
               if scores[area] in (1, 3, 5) else
               "Why: it is the weakest of the four. The rubric does not describe its level, so say "
               "nothing about how it went.")
        timing = " Then give WHERE THE TIME WENT as one short sentence, as written." if has_timing else ""
        return [
            "Write one short paragraph of three or four sentences, under 90 words, like a teacher "
            "giving the trainee one clear priority.",
            f"In the first sentence, in your own words, say that {name} is what to improve first, as "
            f"the weakest of the four areas; use the name \"{name}\" exactly.",
            why + timing,
            f"What to do immediately: state its goal plainly, from the RUBRIC. {no_compare} No practice "
            "or technique suggestions.",
            "Say nothing about the other three areas beyond that they are stronger.",
        ]
    if case == "strongest":
        looked = ("Say what it looked like in its description's own words, not paraphrased."
                  if scores[area] in (1, 3, 5) else
                  "The rubric does not describe its level, so say nothing about how it went.")
        return [
            "Write one short paragraph of two or three sentences, under 80 words.",
            f"In the first sentence, in your own words and warmly, say that {name} is the trainee's "
            f"strongest technical skill, stronger than the other three areas; use the name \"{name}\" exactly.",
            looked,
            "Why it matters: give the reason in WHY IT MATTERS, in your own words, keeping its meaning. "
            "Never say it matters because of where it ranks against the other areas.",
            "Say nothing more about the other areas. No goal, and no suggestions.",
        ]
    raise ValueError(f"unknown plan case {case}")


def build_unit(record: dict[str, Any], template_id: str) -> dict[str, Any]:
    trial = record["trial_level_annotations"]
    scores = {name: int(value) for name, value in trial["grs_subscores"].items()}
    plan = template_plan(template_id, scores)
    if plan is None:
        raise ValueError(
            f"{template_id} cannot be asked of {record['trial_id']} under the ranking strategy "
            "(D5 needs one technique area alone at 5; D2 one alone lowest, at 1 or 3; "
            "D1 an Overall performance of 1, 3 or 5)"
        )
    task_key = record["task"].replace(" ", "_")
    extent = record["trial_extent"]
    span = f"{frame_to_timestamp(extent['start_frame'])}-{frame_to_timestamp(extent['end_frame'])}"
    question = D_QUESTIONS[template_id].replace("[a certain video span]", span)
    timing = time_findings(record, task_key, plan, scores)

    goals = goal_areas(template_id, plan)
    rubric = [line for area in plan["areas"]
              for line in rubric_lines(area, scores[area], task_key, area in goals, template_id != "D1")]
    overall = []
    if template_id == "D1":
        level = scores["overall_performance"]
        overall = [
            "OVERALL (the same surgeon's rating of the whole run, 1 to 5; only 1, 3 and 5 have descriptions)",
            *[f"  {n}: {GRS_RUBRIC['overall_performance'][n]}" for n in (1, 3, 5)],
            f"  This trial: {level}. Open with this verdict, in your own words, keeping its meaning; "
            "never say 'overall performance'.",
        ]
    why = why_it_matters(plan["areas"][0], task_key) if template_id == "D5" else ""
    lines = [
        f"TASK: {record['task']}, on the JIGSAWS bench-top model",
        TASK_DESCRIPTIONS[task_key],
        "",
        "QUESTION",
        question,
        "",
        "ANSWER PLAN",
        *plan_lines(template_id, plan, scores, bool(timing)),
        "The question names the areas by their rating labels; never repeat those labels in the answer.",
        "",
    ]
    if overall:
        lines += [*overall, ""]
    lines += [
        "RUBRIC (JIGSAWS modified global rating score: one expert surgeon rated the whole "
        "trial from 1 to 5; only 1, 3 and 5 have descriptions)",
        *rubric,
    ]
    if why:
        lines += [
            "",
            "WHY IT MATTERS (built from this area's own rubric descriptions; the rubric gives no other "
            "reason, so use only this one)",
            f"  {why}",
        ]
    if timing:
        lines += [
            "",
            f"WHERE THE TIME WENT (this trial's annotated steps timed against {TIME_REFERENCE}; the only "
            "step-level evidence that tracks how efficiently the trainee moved)",
            *timing["lines"],
        ]
    return {
        "trial_id": record["trial_id"],
        "task": record["task"],
        "task_key": task_key,
        "template_id": template_id,
        "plan": plan,
        "scores": scores,
        "span": span,
        "question": question,
        "prompt_body": "\n".join(lines),
        "shown_text": "\n".join([TASK_DESCRIPTIONS[task_key], *overall, *rubric, why]),
        "rubric_shown": {
            area: {str(level): text for level, text in rubric_for(area, task_key).items()}
            for area in plan["areas"]
        },
        "suture_needle_adapted": "suture_needle_handling" in plan["areas"] and task_key in SUTURE_NEEDLE_ADAPTED,
        "timing": timing,
        "goal_areas": goals,
        "why": why,
    }


def user_prompt(unit: dict[str, Any], recent_openers: list[str]) -> str:
    lines = [unit["prompt_body"]]
    if recent_openers:
        lines += ["", "OPENINGS ALREADY USED IN THIS BATCH (open this answer a different way):",
                  *[f'- "{opener}"' for opener in recent_openers]]
    lines += ["", 'Return {"answer": "..."} only.']
    return "\n".join(lines)


def rationale(unit: dict[str, Any], record: dict[str, Any]) -> str:
    """The provenance note, written from the evidence rather than by the model."""
    scores = unit["scores"]
    ratings = ", ".join(f"{GRS_RUBRIC[a]['element']} {scores[a]}" for a in TECHNIQUE_AREAS)
    plan = unit["plan"]
    names = [GRS_RUBRIC[a]["element"] for a in plan["areas"]]
    plan_text = {
        "overall": (f"Overall performance ({scores['overall_performance']}) as the verdict, then only the "
                    "elements rated 4 or 5: a 5 with its level 5 description, a 4 named only; elements "
                    "below 4 are not mentioned"),
        "focus": f"{names[0]} alone is lowest, so it comes first",
        "strongest": f"{names[0]} alone is highest, at {scores[plan['areas'][0]]}",
    }[plan["case"]]
    used = ("Overall performance was used for the opening verdict; Quality of final product was not used"
            if unit["template_id"] == "D1" else "Overall performance and Quality of final product were not used")
    text = (
        "Ratings (JIGSAWS modified global rating score, Gao et al. 2014, Table 3; one expert "
        f"surgeon's rating of the whole trial, 1-5): {ratings}. {used}. Plan: {plan_text}. Quality "
        "statements use the rubric descriptions' own words for ratings of 1, 3 or 5; areas rated 2 or "
        "4, which the rubric does not describe, are not described"
    )
    if unit["suture_needle_adapted"]:
        text += f", with the Suture/needle handling knot wording restated for {record['task']}"
    text += "."
    if unit["why"]:
        text += (f" Why it matters: \"{unit['why']}\", restated from the {names[0]} descriptions in "
                 "Table 3, which gives no reasons of its own.")
    timing, validation = unit["timing"], TIME_VALIDATION[unit["task_key"]]
    if timing:
        ev = timing["evidence"]
        floor = record["gesture_level_evidence"].get("reference_min_time_and_motion", 4)
        steps = "; ".join(
            f"{g['gesture_id']} {GESTURES[g['gesture_id']]}: {g['total_seconds']:.1f} s over "
            f"{g['count']} segment(s) vs median {g['reference_median_total_seconds']:.1f} s over "
            f"{g['reference_median_count']:g}, {g['extra_seconds']:+.1f} s"
            for g in timing["steps"]
        )
        text += (
            f" Timing (from the JIGSAWS gesture transcription; trial time tracks the Time and motion "
            f"rating across {validation['n']} {record['task']} trials, Spearman rho {validation['rho']}, "
            f"p {validation['p']}, and {validation['partial_rho']} with the other three technique ratings "
            f"held fixed): {ev['trial_seconds']:.1f} s vs a median of "
            f"{ev['reference_median_trial_seconds']:.1f} s over {ev['reference_trial_count']} other "
            f"{record['task']} trials rated {floor} or above on Time and motion ({ev['ratio']:.2f}x), "
            f"called \"{TIME_REFERENCE}\" in the answer"
            + (f"; extra time by gesture: {steps}" if steps else "")
            + f"; the steps listed account for {timing['share']:.0%} of the extra time."
        )
    elif "time_and_motion" in plan["areas"] and not validation["validated"]:
        text += (
            f" No timing or kinematic evidence is used: across {validation['n']} {record['task']} "
            f"trials, trial time does not track the Time and motion rating (Spearman rho "
            f"{validation['rho']}, p {validation['p']}), and no other measure tested did."
        )
    if plan["case"] != "focus" or plan["areas"] != ["time_and_motion"]:
        text += (" No gesture or kinematic measure tested tracks Respect for tissue, Suture/needle "
                 "handling or Flow of operation (results/area measure validation - 10-21-2026), so "
                 "none is used for them.")
    return text


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------


def extract_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    return match.group(1).strip() if match else text.strip()


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


def parse_answer(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict) or not isinstance(payload.get("answer"), str):
        raise ValueError('reply must be a JSON object with an "answer" string')
    return payload["answer"].strip()


def first_sentence(text: str, max_words: int = 25) -> str:
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    sentence = match.group(1) if match else text
    words = sentence.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")


def slim_record(out: dict[str, Any]) -> dict[str, Any]:
    """An answer with only what temporal localization needs: the span, question and answer."""
    start, end = (int(frame) for frame in out["source_annotation"]["video_frame_span"].split("-"))
    return {"trial_id": out["trial_id"], "template_id": out["template_id"], "timestamp": out["timestamp_or_frame"],
            "start_frame": start, "end_frame": end, "question": out["question"], "answer": out["qa"][0]["answer"]}


# ---------------------------------------------------------------------------
# Review page
# ---------------------------------------------------------------------------

TEMPLATE_TITLES = {"D1": "D1: Overall feedback", "D2": "D2: Priority and corrective action", "D5": "D5: Positive reinforcement"}
SKILL_WORDS = {"N": "Novice", "I": "Intermediate", "E": "Expert"}


def review_markdown(out_records: list[dict[str, Any]], settings: dict[str, Any], calibration: dict[str, Any]) -> str:
    valid = sum(r["validation_status"] == "valid" for r in out_records)
    lines = [
        f"# Template D coaching answers: {len(out_records)} trials",
        "",
        f"{valid}/{len(out_records)} valid. Model {settings['model_id']}, temperature {settings['temperature']}, "
        "thinking on. Each answer passed the pattern checks and a sentence-by-sentence audit by a second "
        "model call.",
        "",
    ]
    if calibration.get("ran"):
        verdicts = ", ".join(
            f"{c['case']} {'flagged' if c.get('flagged') else 'passed'}"
            f" ({'as expected' if c['correct'] else 'NOT as expected'})"
            for c in calibration["cases"]
        )
        lines += [f"Audit self-test on known paragraphs: {verdicts}.", ""]
    by_template: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in out_records:
        by_template[r["template_id"]].append(r)
    for template_id in ("D1", "D2", "D5"):
        if template_id not in by_template:
            continue
        lines += [f"## {TEMPLATE_TITLES[template_id]}", ""]
        for r in by_template[template_id]:
            s = r["source_annotation"]["grs_subscores"]
            skill = SKILL_WORDS.get(r["source_annotation"]["skill_level"], "?")
            lines += [
                f"### {r['trial_id']}", "",
                f"{skill} (self-reported, not shown to the model). Ratings: tissue {s['respect_for_tissue']}, "
                f"suture/needle {s['suture_needle_handling']}, time and motion {s['time_and_motion']}, flow "
                f"{s['flow_of_operation']}, overall {s['overall_performance']}. Plan: {r['template_plan']['case']}. Timing evidence: "
                f"{'yes' if r['evidence']['timing_used'] else 'no'}.", "",
                f"**Q:** {r['question']}", "",
            ]
            if r["qa"]:
                lines += [f"**A:** {r['qa'][0]['answer']}", "", f"**Rationale:** {r['qa'][0]['rationale']}", ""]
            else:
                lines += [f"**Rejected:** {r['validation_error']}", ""]
            reasons = r["model"]["rejected_attempts_reasons"]
            lines += [f"Attempts: {r['model']['attempts']}" + ("; rejected drafts: " + " | ".join(reasons) if reasons else ""), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inputs", required=True, help="enriched_trial_inputs.jsonl from the builder")
    parser.add_argument("--trial-templates", default=DEFAULT_TRIAL_TEMPLATES)
    parser.add_argument("--system-prompt", default="Prompts_And_Pipeline/system-prompt-jigsaws-enriched-d.md")
    parser.add_argument("--llama-host", default="http://127.0.0.1:18080")
    parser.add_argument("--model-id", default="ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Write the prompts and rationales and stop")
    parser.add_argument("--calibrate-only", action="store_true",
                        help="Run only the audit self-test on the known paragraphs, print it, and stop")
    args = parser.parse_args()

    records = {
        r["trial_id"]: r
        for r in (json.loads(line) for line in Path(args.inputs).read_text(encoding="utf-8").splitlines() if line.strip())
    }
    pairs = [item.strip().split(":") for item in args.trial_templates.split(",") if item.strip()]
    trials = [trial for trial, _ in pairs]
    if len(set(trials)) != len(trials):
        raise SystemExit("each trial may appear once: one question and answer per trial")
    missing = [trial for trial in trials if trial not in records]
    if missing:
        raise SystemExit(f"no enriched input for {missing}; rebuild with --trials")
    units = [build_unit(records[trial], template_id) for trial, template_id in pairs]

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    system_prompt = extract_prompt(Path(args.system_prompt))

    if args.dry_run:
        output.with_name("prompts_dry_run.txt").write_text(
            "\n\n".join(
                f"===== {u['trial_id']} {u['template_id']} ({u['plan']['case']}) =====\n"
                f"{user_prompt(u, [])}\n\n--- rationale ---\n{rationale(u, records[u['trial_id']])}"
                for u in units
            ) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {output.with_name('prompts_dry_run.txt')}")
        return 0

    calibration = calibrate_judge(args.llama_host, units, args.max_tokens)
    if args.calibrate_only:
        output.with_name("audit_self_test.json").write_text(json.dumps(calibration, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(calibration, indent=2))
        return 0 if calibration.get("all_correct") else 1
    print("audit self-test: " + json.dumps(
        {c["case"]: ("flagged" if c.get("flagged") else "passed") + (" OK" if c["correct"] else " WRONG")
         for c in calibration.get("cases", [])}), flush=True)

    openers: dict[str, list[str]] = defaultdict(list)
    out_records: list[dict[str, Any]] = []
    with output.open("w", encoding="utf-8") as handle:
        for index, unit in enumerate(units, 1):
            record = records[unit["trial_id"]]
            base_prompt = user_prompt(unit, openers[unit["template_id"]][-RECENT_OPENERS_TO_SHOW:])
            prompt, answer, raw, reasoning, error, judge_raw = base_prompt, None, "", None, None, ""
            rejected: list[str] = []
            started = time.time()
            attempts = 0
            for attempts in range(1, args.max_retries + 2):
                try:
                    raw, reasoning = call_llama(args.llama_host, system_prompt, prompt, args.temperature, args.max_tokens)
                    candidate = parse_answer(raw)
                    problems = check_answer(candidate, unit, openers[unit["template_id"]])
                    if not problems:
                        audit, judge_raw = run_judge(args.llama_host, unit, candidate, args.max_tokens)
                        problems = [f"audit rule {p.get('rule')}: \"{p.get('quote', '')}\" ({p.get('why', '')})" for p in audit]
                    if not problems:
                        answer, error = candidate, None
                        break
                    error = "; ".join(problems)
                    rejected.append(error)
                    hint = ("\n" + LABEL_HINT) if any("rating label" in r for r in rejected) else ""
                    earlier = [r for r in dict.fromkeys(rejected[:-1]) if not re.match(r"\w*(?:Error|Exception)\b", r)]
                    prompt = (base_prompt + "\n\nYOUR PREVIOUS ANSWER WAS REJECTED\n" + f'"{candidate}"\n'
                              + "Problems: " + error + "." + hint + "\n"
                              + ("Earlier drafts were also rejected for: " + " | ".join(earlier) + ".\n" if earlier else "")
                              + "Rewrite it to fix these problems, keeping to the rubric and timing above.")
                except (ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    rejected.append(error)
            status = "valid" if answer is not None else "rejected"
            if answer is not None:
                openers[unit["template_id"]].append(first_sentence(answer))
            trial = record["trial_level_annotations"]
            extent = record["trial_extent"]
            evidence = record["gesture_level_evidence"]
            timing = unit["timing"]
            out = {
                "qa_id": f"jigsaws_{unit['task_key'].lower()}_{unit['template_id'].lower()}_{unit['trial_id']}_coaching",
                "dataset": "JIGSAWS",
                "procedure_or_task": unit["task_key"],
                "generation_mode": "rubric_and_validated_timing",
                "template_id": unit["template_id"],
                "template_plan": unit["plan"],
                "trial_id": unit["trial_id"],
                "timestamp_or_frame": unit["span"],
                "question": unit["question"],
                "source_annotation": {
                    "skill_level": trial["skill_level"],
                    "grs_total": trial["grs_total"],
                    "grs_subscores": trial["grs_subscores"],
                    "not_shown_to_model": ["skill_level", "grs_total", "quality_of_final_product"]
                    + ([] if unit["template_id"] == "D1" else ["overall_performance"]),
                    "video_frame_span": f"{extent['start_frame']}-{extent['end_frame']}",
                    "gesture_count": extent["gesture_count"],
                    "representation_version": record["representation_version"],
                },
                "evidence": {
                    "rubric_shown": unit["rubric_shown"],
                    "rubric_source": "Gao et al. 2014, Table 3",
                    "suture_needle_adapted": unit["suture_needle_adapted"],
                    "timing_used": bool(timing),
                    "timing_lines": timing["lines"] if timing else [],
                    "timing_steps": timing["steps"] if timing else [],
                    "time_evidence": evidence.get("time_evidence"),
                    "time_validation": TIME_VALIDATION[unit["task_key"]],
                    "reference_trial_ids": evidence["reference_trial_ids"],
                    "reference_min_time_and_motion": evidence.get("reference_min_time_and_motion"),
                    "finding_details": [],
                },
                "model": {
                    "backend": "real", "inference_engine": "llama", "model_id": args.model_id,
                    "temperature": args.temperature, "max_tokens": args.max_tokens,
                    "thinking": {"enabled": True, "trace_returned": bool(reasoning)},
                    "attempts": attempts,
                    "rejected_attempts_reasons": rejected if answer is not None else rejected[:-1],
                    "system_prompt_path": args.system_prompt,
                    "prompt": prompt, "raw_output": raw, "reasoning_content": reasoning,
                    "audit_raw_output": judge_raw,
                    "elapsed_seconds": round(time.time() - started, 1),
                },
                "qa": ([{"question": unit["question"], "answer": answer, "rationale": rationale(unit, record)}]
                       if answer is not None else []),
                "validation_status": status,
                "validation_error": None if answer is not None else error,
            }
            out_records.append(out)
            handle.write(json.dumps(out, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"[{index}/{len(units)}] {unit['trial_id']} {unit['template_id']}: {status} "
                  f"after {attempts} attempt(s), {out['model']['elapsed_seconds']}s", flush=True)

    settings = {"model_id": args.model_id, "temperature": args.temperature}
    output.with_name("review.md").write_text(review_markdown(out_records, settings, calibration) + "\n", encoding="utf-8")
    slim = [slim_record(r) for r in out_records if r["qa"]]
    output.with_name("qa_slim.json").write_text(json.dumps(slim, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {
        "records": len(out_records),
        "valid": sum(r["validation_status"] == "valid" for r in out_records),
        "rejected": sum(r["validation_status"] != "valid" for r in out_records),
        "audit_self_test": calibration,
        "trial_templates": args.trial_templates,
        "output": str(output),
    }
    output.with_name("run_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "audit_self_test"}, indent=2))
    return 0 if summary["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
