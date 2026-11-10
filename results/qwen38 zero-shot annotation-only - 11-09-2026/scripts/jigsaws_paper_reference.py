#!/usr/bin/env python3
"""Text taken verbatim from the JIGSAWS paper, for prompts and rationales.

Source: Gao et al., "The JHU-ISI Gesture and Skill Assessment Working Set
(JIGSAWS): A Surgical Activity Dataset for Human Motion Modeling", M2CAI MICCAI
Workshop 2014 (https://cirl.lcsr.jhu.edu/wp-content/uploads/2015/11/JIGSAWS.pdf).
Section 2.1 (tasks), Section 2.4 and Table 1 (kinematics), Table 2 (gestures),
Table 3 (GRS rubric, page 7). Only line-break hyphens and the "ffi" ligature were
undone. Anything not verbatim is marked as an adaptation.
"""
from __future__ import annotations

# Table 3. Elements of modified global rating score. The paper writes anchors
# for 1, 3 and 5 only; 2 and 4 have no text of their own.
GRS_RUBRIC = {
    "respect_for_tissue": {
        "element": "Respect for tissue",
        1: "Frequently used unnecessary force on tissue",
        3: "Careful tissue handling but occasionally caused inadvertent damage",
        5: "Consistent appropriate tissue handling",
    },
    "suture_needle_handling": {
        "element": "Suture/needle handling",
        1: "Awkward and unsure with repeated entanglement and poor knot tying",
        3: "Majority of knots placed correctly with appropriate tension",
        5: "Excellent suture control",
    },
    "time_and_motion": {
        "element": "Time and motion",
        1: "Made unnecessary moves",
        3: "Efficient time/motion but some unnecessary moves",
        5: "Clear economy of movement and maximum efficiency",
    },
    "flow_of_operation": {
        "element": "Flow of operation",
        1: "Frequently interrupted flow to discuss the next move",
        3: "Demonstrated some forward planning and reasonable procedure progression",
        5: "Obviously planned course of operation with efficient transitions between moves",
    },
    "overall_performance": {
        "element": "Overall performance",
        1: "Very poor", 3: "Competent", 5: "Clearly Superior",
    },
    "quality_of_final_product": {
        "element": "Quality of final product",
        1: "Very poor", 3: "Competent", 5: "Clearly Superior",
    },
}

# ADAPTATION, not paper text. The suture/needle handling anchors describe knots,
# but only Knot Tying ties one; Suturing and Needle Passing were rated on the same
# element. Per the user's decision of 2026-10-13, the knot wording is restated
# for those tasks and everything else is kept. Knot Tying uses Table 3 as written.
SUTURE_NEEDLE_ADAPTED = {
    "Suturing": {
        1: "Awkward and unsure with repeated entanglement and poor suturing",
        3: "Majority of sutures placed correctly with appropriate tension",
        5: "Excellent suture control",
    },
    "Needle_Passing": {
        1: "Awkward and unsure with repeated entanglement and poor needle passing",
        3: "Majority of needle passes made correctly with appropriate movement",
        5: "Excellent suture control",
    },
}

# ADAPTATION, not paper text. Table 3 gives no reason why any element matters, only
# what levels 1, 3 and 5 look like. D5 asks why the strongest skill matters (the
# user's request of 2026-10-21: say why the area matters from its own rubric), so
# each reason below is built from that element's Table 3 descriptions and nothing
# else. "levels" lists the descriptions each one draws on.
WHY_IT_MATTERS = {
    "respect_for_tissue": {
        "levels": (1, 3, 5),
        "text": "Handling the tissue appropriately matters because it keeps unnecessary force and "
                "inadvertent damage off the tissue.",
    },
    "suture_needle_handling": {
        "levels": (1, 3, 5),
        "text": {
            "Knot_Tying": "Control of the needle and suture matters because it places the knots correctly "
                          "with appropriate tension, without repeated entanglement.",
            "Suturing": "Control of the needle and suture matters because it places the sutures correctly "
                        "with appropriate tension, without repeated entanglement.",
            "Needle_Passing": "Control of the needle and suture matters because it makes the needle passes "
                              "correctly with appropriate movement, without repeated entanglement.",
        },
    },
    "time_and_motion": {
        "levels": (1, 5),
        "text": "Moving efficiently matters because it means economy of movement, without unnecessary moves.",
    },
    "flow_of_operation": {
        "levels": (1, 5),
        "text": "Planning your course of operation matters because it gives efficient transitions between "
                "moves, instead of interrupting the flow to discuss the next move.",
    },
}


def why_it_matters(area: str, task: str) -> str:
    text = WHY_IT_MATTERS[area]["text"]
    return text[task] if isinstance(text, dict) else text


# Section 2.1, Surgical tasks.
TASK_DESCRIPTIONS = {
    "Suturing": (
        "The subject picks up needle, proceeds to the incision (designated as a vertical "
        "line on the bench-top model), and passes the needle through the “tissue”, "
        "entering at the dot marked on one side of the incision and exiting at the "
        "corresponding dot marked on the other side of the incision. After the first "
        "needle pass, the subject extracts the needle out of the tissue, passes it to the "
        "right hand and repeats the needle pass three more times."
    ),
    "Knot_Tying": (
        "The subject picks up one end of a suture tied to a flexible tube attached at its "
        "ends to the surface of the bench-top model, and ties a single loop knot."
    ),
    "Needle_Passing": (
        "The subject picks up the needle (in some cases not captured in the video) and "
        "passes it through four small metal hoops from right to left. The hoops are "
        "attached at a small height above the surface of the bench-top model."
    ),
}

# Table 2. Gesture vocabulary.
GESTURES = {
    "G1": "Reaching for needle with right hand",
    "G2": "Positioning needle",
    "G3": "Pushing needle through tissue",
    "G4": "Transferring needle from left to right",
    "G5": "Moving to center with needle in grip",
    "G6": "Pulling suture with left hand",
    "G7": "Pulling suture with right hand",
    "G8": "Orienting needle",
    "G9": "Using right hand to help tighten suture",
    "G10": "Loosening more suture",
    "G11": "Dropping suture at end and moving to end points",
    "G12": "Reaching for needle with left hand",
    "G13": "Making C loop around right hand",
    "G14": "Reaching for suture with right hand",
    "G15": "Pulling suture with both hands",
}

# Section 2.4 and Table 1: "PSM1 and PSM2, also referred as the right and left PSMs
# in this dataset"; columns 39-57 are PSM1 and 58-76 are PSM2 (1-based). The
# readme's "Slave left" label for columns 39-57 contradicts this; the paper and the
# gesture labels agree (see build_jigsaws_enriched_pilot.SIDE_COLUMNS).
PSM_SIDES = {"PSM1": "right", "PSM2": "left"}
FPS = 30  # Section 2.4: kinematics and video captured at 30 Hz


def rubric_for(area: str, task: str) -> dict[int, str]:
    """The 1/3/5 anchors shown for one element of one task's trial."""
    if area == "suture_needle_handling" and task in SUTURE_NEEDLE_ADAPTED:
        return dict(SUTURE_NEEDLE_ADAPTED[task])
    return {level: GRS_RUBRIC[area][level] for level in (1, 3, 5)}
