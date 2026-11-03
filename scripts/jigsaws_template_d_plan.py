#!/usr/bin/env python3
"""The Template D ranking strategy, importable on its own.

Decides which technique areas a D1, D2 or D5 answer is about, and whether D1, D2
or D5 may be asked of a trial at all. It began as a copy of the ranking strategy in
scripts/run_annotation_qa_jigsaws.py on the claude/template-d-jigsaw-ratings-a100fa
worktree, which produced the 9-27 sample and the 9-30 full run. The user's rules of
2026-10-21 have changed it since: D1 only where Overall performance is 1, 3 or 5, and
D2 and D5 only where the lowest or highest area sits at a level the rubric describes.

The question wording is that worktree's Prompts_And_Pipeline/template-questions-A-D.md,
the text the 9-30 run used: D3 was merged into D2, and D4 was removed.
"""
from __future__ import annotations

from typing import Any


D_QUESTIONS = {
    "D1": (
        "If you were the supervising surgeon, based only on the events occurring "
        "during [a certain video span], what feedback would you give on the trainee's "
        "tissue handling, instrument/needle handling, economy of motion, and flow of "
        "operation?"
    ),
    "D2": (
        "Given the trainee's performance during [a certain video span], among tissue "
        "handling, instrument/needle handling, economy of motion, and flow of "
        "operation, which area should the trainee improve first, why, and what should "
        "the trainee do immediately to address it?"
    ),
    "D5": (
        "Based on this trainee's performance, what is their strongest technical "
        "skill, and why does it matter?"
    ),
}

# The only GRS elements D1, D2 and D5 may name. Overall performance is a global
# rating rather than a skill, and quality of the final product is the result of
# the other four, so neither is an area to fix first or a technique to praise.
TECHNIQUE_AREAS = [
    "respect_for_tissue",
    "suture_needle_handling",
    "time_and_motion",
    "flow_of_operation",
]

GRS_PLAIN_LABELS = {
    "respect_for_tissue": "tissue handling",
    "suture_needle_handling": "instrument/needle handling",
    "time_and_motion": "economy of motion",
    "flow_of_operation": "flow of operation",
}

# A 4 or 5 is a strength; a 3 is not. The OSATS anchors this GRS derives from
# describe a 3 with its flaws still attached ("some unnecessary moves",
# "occasionally appeared stiff or awkward"), and a 3 is called intermediate.
STRONG_SCORE = 4

JIGSAWS_FPS = 30


def frame_to_timestamp(frame: int, fps: int = JIGSAWS_FPS) -> str:
    total_seconds = round(frame / fps)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def area_level(score: int) -> str:
    """How feedback on one technique area is pitched: 1-2 low, 3 intermediate, 4-5 strong."""
    if score >= STRONG_SCORE:
        return "strong"
    return "intermediate" if score == 3 else "low"


def d5_strongest_area(grs_subscores: dict[str, int]) -> str | None:
    """The one technique area D5 may praise, or None when D5 should not be asked.

    D5 asks for the trainee's strongest technical skill and why it matters, so it
    needs one area that is stronger than every other (a tie gives "the strongest"
    several answers), rated 5. A 4 has no rubric description, so it is never the
    strongest ("Do not include level 2 and 4 if they are the lowest or highest",
    the user, 2026-10-21). In JIGSAWS only flow of operation is ever alone at 5,
    and only in Suturing.
    """
    top = max(grs_subscores[name] for name in TECHNIQUE_AREAS)
    at_top = [name for name in TECHNIQUE_AREAS if grs_subscores[name] == top]
    if top != 5 or len(at_top) > 1:
        return None
    return at_top[0]


def improvement_plan(grs_subscores: dict[str, int]) -> tuple[str, list[str]] | None:
    """The one area D2 names, or None when D2 should not be asked.

    D2 asks which area to improve first, which presupposes there is one. It is
    asked only when a single technique area is strictly lowest, at 1 or 3.
    Several areas sharing the lowest score (Knot_Tying_C001 at 3/3/3, Needle_Passing_D001
    at 1/1/1) means no D2, as a tie at the top means no D5 -- the user's rule of
    2026-10-14, replacing the earlier tied, all_low and all_intermediate cases that
    named several areas together. A lowest at 2 has no rubric description, so it means
    no D2 either ("Do not include level 2 and 4 if they are the lowest or highest", the
    user, 2026-10-21). None also when every area is 4 or 5.
    """
    scores = {name: grs_subscores[name] for name in TECHNIQUE_AREAS}
    low = min(scores.values())
    lowest = [name for name in TECHNIQUE_AREAS if scores[name] == low]
    if low not in (1, 3) or len(lowest) > 1:
        return None
    return "focus", lowest


def template_plan(template_id: str, grs_subscores: dict[str, int]) -> dict[str, Any] | None:
    """The case and areas a D1, D2 or D5 answer must follow, recorded with each record."""
    if template_id == "D1":
        # D1 opens with the Overall performance verdict (the user's format of 2026-10-21),
        # which the rubric describes only at 1, 3 and 5.
        if grs_subscores["overall_performance"] not in (1, 3, 5):
            return None
        return {
            "case": "overall",
            "areas": list(TECHNIQUE_AREAS),
            "levels": {name: area_level(grs_subscores[name]) for name in TECHNIQUE_AREAS},
        }
    if template_id == "D2":
        plan = improvement_plan(grs_subscores)
        return None if plan is None else {"case": plan[0], "areas": plan[1]}
    if template_id == "D5":
        area = d5_strongest_area(grs_subscores)
        return None if area is None else {"case": "strongest", "areas": [area]}
    return None


def join_labels(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"
