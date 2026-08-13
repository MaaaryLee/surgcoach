#!/usr/bin/env python3
"""Generate event-grounded QA: one question per localized moment, not per trial.

Written against review feedback that the Type C/D answers were generic. The cause
was measured rather than guessed: 7 of our 10 templates ask a literally identical
question for every trial, differing only in the timestamp, so nothing in the input
distinguishes one moment from another and nothing in the output can.

The question is short and scoped to one span and one aspect of the task -- "At
0:05.5-0:19.5 the trainee was reaching for the needle with the right hand. How was
their needle handling there?" -- and the annotation for that span is supplied to the
answerer separately, in the prompt, never in the question.

That split matters and the first version got it wrong. Putting the measurement into
the question made the question verifiable and the answer pointless: 24% of an
answer's content words already appeared in its own question, against 6% for the older
trial-level templates, so the answer could only paraphrase and append advice. It also
made every answer sound alike, since a question that already contains the finding
leaves nothing else to say. And as training data such a pair is close to worthless --
the finding is recoverable from the question text without seeing the video at all.

The answer is still checkable, but against the withheld annotation rather than
against its own question, which is what an LLM-as-judge pass is for.

Question kinds, built from scripts/localize_events.py output plus the
transcription. Only the first two are generated: see VALIDATED_KINDS below for the
correlations that retired the other three.

  outlier      a span that took far longer than the same gesture takes in the
               strongest trials                                    rho -0.44
  wandering    a span where the instrument travelled much further than it does in
               the strongest trials                                rho -0.35
  ---- retired, no measurable relationship to assessed skill ----
  repetition   a gesture the trial returned to several separate times    +0.04
  economy      total gesture-segment count against the task median       +0.05
  clean        a span that beat the comparison                           +0.05

Two inference backends, because the two machines this runs on differ. Locally it
talks to Ollama over HTTP against a 4-bit quantisation; on the cluster there is no
Ollama and the model is loaded in-process at full precision through transformers.
The questions, the prompt and the record schema are identical either way, so the
only variable between the two is the model itself.

    python3 scripts/generate_event_qa.py --task Knot_Tying --trial Knot_Tying_G004
    python3 scripts/generate_event_qa.py --trials Suturing_C004,Knot_Tying_G004 -o outputs/event_qa
    python3 scripts/generate_event_qa.py --task Suturing --backend transformers
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from localize_events import (FPS, GESTURES, build_baselines, events_for_trial,
                             read_kinematics, read_meta, read_spans, reference_trials,
                             resolve_jigsaws_root, resolve_task_root, span_metrics,
                             timestamp)
from run_annotation_qa_jigsaws import (TRANSPORT_BACKOFF_SECONDS, TRANSPORT_RETRIES,
                                       extract_system_prompt, load_text_model,
                                       parse_json_payload, run_generation)

# The annotation goes here, in the prompt, and NOT in the question.
#
# The first version put the measurement into the question text -- "the instrument
# travelled 5.6 times further than in the strongest recorded attempts" -- which made
# the question verifiable and the answer pointless. Measured: 24% of an answer's
# content words already appeared in its own question, against 6% for the older
# trial-level templates. The answer had nothing left to contribute but a paraphrase
# and some generic advice, which is also the mechanism behind every answer sounding
# alike. As training data a pair like that is close to worthless: the finding can be
# recovered from the question without ever seeing the video.
#
# So the question is short and scoped to one span and one aspect of the task, and the
# finding is supplied here for the answer to state.
USER_PROMPT = (
    "A question about one moment of a bench-top surgical training recording. No "
    "frames are attached.\n\n"
    "What the annotations record for this span. The question deliberately withholds "
    "it, so this is yours to report:\n"
    "{facts}\n\n"
    "The question (copy it verbatim into the question field):\n{question}\n\n"
    "Answer it in the third person. The trainee is being described, not addressed, "
    "so \"you\" and \"your\" must not appear. Vary how the subject is named -- \"the "
    "trainee\", \"they\", or the action itself (\"that transfer\", \"the second "
    "attempt\") -- rather than opening every answer the same way. Because the "
    "question does not contain the finding, the answer has to state it; an answer "
    "that only restates the question has said nothing.\n\n"
    "{guidance}\n\n"
    "Return valid JSON only, following the system prompt schema.")

# Which aspect of the task a question is scoped to, per gesture. Naming an aspect is
# what stops "what feedback would you give" running in any direction at all, without
# handing over the finding itself.
GESTURE_ASPECT = {
    "G1": "needle handling", "G12": "needle handling",
    "G2": "needle handling", "G8": "needle handling",
    "G3": "needle handling", "G4": "needle handling", "G5": "needle handling",
    "G6": "suture handling", "G7": "suture handling", "G9": "suture handling",
    "G10": "suture handling", "G11": "suture handling",
    "G13": "suture handling", "G14": "suture handling", "G15": "suture handling",
}

# Short, and scoped to one span and one aspect. Third person throughout, question
# and answer alike, matching the A-D templates so the whole dataset reads
# consistently.
#
# The older wording ("What does that extra travel suggest, and what single change
# would tighten it?") reads as an examiner testing a candidate on a fact the examiner
# has just supplied, which is the problem being fixed here.
#
# None of these may be a yes/no. "Was there anything wrong with their needle
# handling?" was the second variant here and is gone: a question that offers a
# fault as one of two answers has already told the model which one is interesting,
# and it constrains the answer to confirming or denying rather than describing. An
# open ask costs nothing and presupposes nothing.
SCOPED_ASKS = (
    "At {span} the trainee was {gesture}. How was their {aspect} there?",
    "At {span} the trainee was {gesture}. Assess their {aspect} over that span.",
    # No "what would you say about ...". The "you" there is the attending being
    # asked, not the trainee, so it is not a person error -- but the answer is now
    # required to contain no "you" at all, and a pronoun sitting in the question is
    # exactly the kind of seed that has come back verbatim three times in this
    # project. Cheaper to remove it than to check for the consequence.
    "Looking at {span}, where the trainee was {gesture}: what is there to say about "
    "their {aspect}?",
)

# --anchor ordinal: the same questions with the clock removed, the moment named by
# which occurrence of the gesture it was.
#
# Two reviewers reached this from opposite directions. One asked whether the moment
# could be named by what was happening rather than by a timestamp -- and JIGSAWS does
# annotate that; it is the gesture label already in every question. The other observed
# that handing over the timestamp deletes the localization half of the task: a model
# given "0:05.5-0:19.5" never has to find the moment, which for a video model is most
# of the work.
#
# An ordinal serves both: it names the moment in content terms and still requires the
# model to locate it. The ordinal is necessary rather than decorative -- across all
# 103 trials there is not one where every gesture occurs exactly once, the median
# trial repeats some gesture four times, and Suturing_G001 repeats one ten times. So
# "during the needle positioning" is ambiguous in every trial in the dataset.
ORDINAL_ASKS = (
    "The trainee was {gesture} several times in this recording. On the {ordinal} of "
    "those, how was their {aspect}?",
    "Find the {ordinal} time the trainee was {gesture} and assess their {aspect} "
    "there.",
    "Looking at the {ordinal} time the trainee was {gesture}: what is there to say "
    "about their {aspect}?",
)
# For a gesture that occurs once in the trial, where an ordinal would imply others.
UNIQUE_ASKS = (
    "At the point where the trainee was {gesture}, how was their {aspect}?",
    "Find the point where the trainee was {gesture} and assess their {aspect} there.",
)
ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh",
            "eighth", "ninth", "tenth", "eleventh", "twelfth")

# --anchor none: the locate question, which names no moment at all.
#
# There was one of these, hardcoded, for the whole of the last three batches. Review
# feedback: "both of these use pretty rigid templates that don't have much variation
# within them ... we should try to increase the diversity of how the questions are
# formulated either by using more templates or asking the LLM to be more flexible".
# Measured before the fix: 1 sentence frame across 8 questions, and 4 frames across
# the 5 ordinal ones.
#
# Produced with an LLM offline, then filtered and curated. Review asked for exactly
# that, and it is safe in a way that rewriting questions at generation time is not: the
# paraphrasing model saw only the base sentence with a {label} placeholder -- no record,
# no annotation, no finding -- so there was nothing for it to leak. The answering model
# still never writes its own question. 189 candidates were generated across 24
# structure-briefed rounds; 162 failed an automated check and the rest were curated by
# hand. Reproduce with the script in scratchpad/paraphrase_templates.py.
#
# Every entry meets four requirements, all enforced in check_event_qa.py:
#
#   the frame            "the trainee was {label}" or "shows the trainee was {label}".
#                        {label} is a gerund phrase, so nothing else survives
#                        substitution: "the trainee being {label}" expands to "being
#                        pushing the needle through the tissue".
#   a yes/no gate        the question must permit "none of them". This is the one that
#                        matters most and the one the LLM broke most often: "Which
#                        occurrence involved a mistake?" reads like a clean paraphrase
#                        and presupposes a fault, which is wrong for roughly three
#                        quarters of the records this builder produces.
#   an explicit "which"  or it is not this question.
#   recurrence           stated, or "which one" ranges over nothing.
#
# SIX, not the seventeen the first pass produced. Those seventeen passed every check
# and were rejected on review for sounding the same, correctly: with the four
# requirements above all in force, the only freedom left is the adjectives, so they
# varied by mistake/error/fault and multiple/several/repeatedly and nothing else. These
# six differ in sentence type -- where the question sits, how many sentences it takes,
# and whether it is a question at all. That is the ceiling for this question form;
# beyond it, one of the four requirements would have to be dropped, and none can be.
LOCATE_ASKS = (
    # statement, then yes/no, then elliptical wh. The wording review approved.
    "The trainee was {label} multiple times in this recording. Did they make a "
    "mistake during one or more of those? If so, which?",
    # one sentence: conditional protasis, wh apodosis
    "The trainee was {label} several times in this recording; if any of those went "
    "wrong, which were they?",
    # presentational opening, then yes/no, then an imperative closer
    "This recording shows the trainee was {label} on several occasions. Did any of "
    "those go wrong? Name it if so.",
    # an imperative instruction with an embedded interrogative -- no question mark
    "Review each of the several times the trainee was {label} in this recording, and "
    "report whether a mistake occurred in any of them and which.",
    # a single wh-question, the gate carried by a parenthetical "if any"
    "Which of the several times the trainee was {label} in this recording, if any, "
    "involved a mistake?",
    # relative-clause subject, the fault as a predicate adjective
    "Of the occasions in this recording where the trainee was {label}, were any "
    "faulty, and which?",
)


# Which hedge marks an inference as an inference, assigned per record rather than
# chosen by the model.
#
# The system prompt has listed these alternatives since the batch where "most likely"
# appeared in 26 of 27 answers. Listing them did not work: the next batch put "follows
# from" in 62%. The reason is structural rather than a failure to follow instructions
# -- the model answers one question at a time and has no way to see that every other
# answer reached for the same item on the list. A choice it cannot observe the
# consequences of is not a choice it can vary.
#
# So the caller makes it. Assignment is by hash of the record's own seed, which keeps
# a rerun of the same trial identical and so keeps two batches comparable, the same
# property scoped_question() relies on.
HEDGES = (
    "the usual cause of this is",
    "this is what happens when",
    "that pattern points to",
    "consistent with",
    "typically this follows from",
    "most likely",
)


# How the answer opens, assigned per record for the same reason the hedge is.
#
# Rule 10 tells the model to vary how it names the subject and not to begin every
# answer the same way. It does not work, for the structural reason that defeated the
# hedge list: the model answers one question at a time and cannot see that 22 other
# answers opened identically. In job 29510, 23 of 38 answers (61%) began "The
# trainee". In job 29502 only 14 did, and the variety there was an accident -- it came
# from a worked example in the guidance that also caused three factually wrong answers,
# so removing it fixed one problem and created this one.
#
# Deliberately abstract. Not one of these contains an action noun or verb, because the
# example that seeded "the four pulls went cleanly" is exactly how a pushing gesture
# came to be described as pulling. These name a SHAPE for the first few words and
# leave the vocabulary to the record.
# Five each, not four, and only one of the five is "The trainee". With four it drew 9
# of the 25 clean slots by hash luck -- 36%, against a 40% batch check -- which leaves
# no headroom for the model also reaching for it when a different opening was assigned.
# The last entry in each bank forbids the word outright, which is the only option that
# cannot degrade back toward the default.
FAULT_OPENINGS = (
    "Open by naming which occurrence it was.",
    "Open with \"The trainee\".",
    "Open by naming the action, in the question's own words, before saying which "
    "occurrence went wrong.",
    "Open with a phrase that places the moment in the sequence, before the subject.",
    "Do not use the word \"trainee\" anywhere in the answer; refer to the occurrence "
    "or the action instead.",
)
CLEAN_OPENINGS = (
    "Open by naming the action, in the question's own words.",
    "Open with \"The trainee\".",
    "Open by saying how many there were, then name the action.",
    "Open with \"All of them\" or \"Each of them\".",
    "Do not use the word \"trainee\" anywhere in the answer; name the action instead.",
)


def opening_for(seed: str, clean: bool) -> str:
    """Which opening shape this record must use, so the batch varies.

    Mixed differently again from hedge_for and the template pick, so three choices
    driven by one seed do not move together.
    """
    bank = CLEAN_OPENINGS if clean else FAULT_OPENINGS
    h = 0
    for c in seed:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return bank[h % len(bank)]


def hedge_for(seed: str) -> str:
    """The hedge this record must use, so the batch varies even though answers cannot.

    Weighted by position rather than the plain character sum scoped_question() uses.
    Both would otherwise be functions of the same total, and since 6 is a multiple of
    the 3-template banks the two picks would move in lockstep -- template variant 0
    would only ever appear with two of the six hedges.
    """
    return HEDGES[sum(ord(c) * (i + 1) for i, c in enumerate(seed)) % len(HEDGES)]

TASK_OF = {"Suturing": "Suturing", "Knot_Tying": "Knot_Tying", "Needle_Passing": "Needle_Passing"}

# Only kinds whose underlying measure demonstrably tracks assessed skill. Run
# scripts/validate_detectors.py to reproduce; Spearman rho against grs_total over
# all 103 trials:
#
#   outlier      slow spans              -0.44   works
#   wandering    excess travel           -0.35   works
#   ------------------------------------------------------------------
#   economy      gesture-segment count   +0.05   no relationship
#   repetition   max gesture repeats     +0.04   no relationship
#   clean        fast spans              +0.05   no relationship
#
# The three excluded kinds were not wrong about their own numbers -- a trial really
# did use 16 segments against a median of 10. They were wrong to call that "economy
# of motion", because segment count turns out to say nothing about how the trial
# was graded. A question is only as good as the link between what it measures and
# what it claims to be about.
#
# "clean" is the painful one: the reviewer explicitly asked for localized good
# executions, and this says we cannot currently find them. Faults are detectable
# because taking four times too long is unambiguous; being quick at a step is not
# evidence of skill, and on weak trials it is usually a fragmented gesture. Better
# to report that than to ship 10 records asserting it.
#
# To generate a retired kind anyway -- for measurement, not for output -- pass a
# wider tuple to build_questions(kinds=...). There is deliberately no command-line
# flag: re-enabling these should require editing code and reading this comment.
VALIDATED_KINDS = ("outlier", "wandering")

# --- questions built from recorded errors rather than inferred from motion -------
#
# The kinds above localize a moment from timing and distance, so the question can
# state that a span was slow but has to ask the model to guess why. These use the
# UVA-DSA consensus error labels, where a human recorded what actually went wrong,
# per gesture instance, with frame bounds that match the transcription exactly.
# See scripts/error_labels.py.
#
# What each error type licenses being asked. Written per type deliberately: one
# question template across all of them would reproduce the boilerplate problem,
# where every answer converged on "commit to a single direct path" because every
# question was really the same question.
# The asks below deliberately no longer contain the words "most likely". They did,
# in two of the three, and "most likely" then appeared in 26 of 27 answers -- the
# instruction handing the model the exact phrase the system prompt was simultaneously
# asking it to vary. The hedge is now assigned per record instead; see hedge_for().
ERROR_QUESTIONS = {
    "Multiple Attempts": (
        "the step needed several attempts before it succeeded",
        "Describe it in terms of the attempts: what made the earlier ones fail where "
        "the last one worked."),
    "Needle Drop": (
        "the needle came out of the instrument's grasp",
        "Describe it in terms of the hold on the needle: what let it escape at this "
        "point in the step."),
    "Needle Orientation": (
        "the needle was presented at the wrong orientation for the step",
        "Describe it in terms of how the needle was presented: how its orientation "
        "differed from what the step needs."),
}
# Excluded, by decision rather than omission. Out of View is 57% of all labelled
# errors, but it records that an instrument left the camera frame -- a framing and
# awareness problem whose only coaching is "keep it in view". Generating from it
# would reintroduce exactly the one-note answer problem that these per-type
# questions exist to avoid. Excluding it takes 377 labelled errors down to 162.
EXCLUDED_ERROR_TYPES = ("Out of View", "Any error", "Needle Curvature (draft)")

# Which error leads when one span carries two. 29 spans in the dataset do, and the
# first version took whichever the annotation files happened to list first -- which
# put Multiple Attempts in front of Needle Drop in every such case, burying the
# rarest and most specific label in the dataset (6 instances in total) behind the
# commonest.
#
# Ordered by how much each explains rather than by frequency. A dropped needle is a
# cause: it is why the step had to be attempted again. "Several attempts" is what
# that looks like from outside. Leading with the cause gives the answer somewhere
# specific to go; leading with the symptom invites the generic "commit to one
# attempt" that this whole batch is trying to get away from.
ERROR_SPECIFICITY = ("Needle Drop", "Needle Orientation", "Multiple Attempts")


def occurrence_index(spans: list[tuple[int, int, str]], start: int, gesture: str) -> tuple[int, int]:
    """Which occurrence of this gesture the span is, and how many there are."""
    same = sorted(s for s, _, g in spans if g == gesture)
    return (same.index(start) + 1 if start in same else 0), len(same)


def scoped_question(span_label: str, gesture: str, seed: str,
                    anchor: str = "timestamp",
                    ordinal: int = 0, total: int = 0) -> str:
    """The question: one moment, one aspect, and no finding in it.

    anchor "timestamp" names the span by the clock; "ordinal" names it by which
    occurrence of the gesture it was, leaving the model to find it.

    The variant is chosen from a hash rather than at random so a rerun of the same
    trial produces the same question and two batches stay comparable.
    """
    aspect = GESTURE_ASPECT.get(gesture, "technique")
    label = GESTURES.get(gesture, gesture)
    pick = sum(map(ord, seed))
    if anchor == "ordinal":
        # A gesture occurring once needs no ordinal; one occurring beyond the words
        # we have falls back to the timestamp rather than inventing "thirteenth".
        if total <= 1:
            return UNIQUE_ASKS[pick % len(UNIQUE_ASKS)].format(gesture=label, aspect=aspect)
        if 1 <= ordinal <= len(ORDINALS):
            return ORDINAL_ASKS[pick % len(ORDINAL_ASKS)].format(
                ordinal=ORDINALS[ordinal - 1], gesture=label, aspect=aspect)
    return SCOPED_ASKS[pick % len(SCOPED_ASKS)].format(
        span=span_label, gesture=label, aspect=aspect)


def reference_peers(meta: dict, trial: str) -> list[str]:
    """The comparison group: strongest trials by score, excluding this one."""
    return reference_trials(meta, exclude=trial)


def build_localization_questions(task: str, trial: str, root: Path,
                                 labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One question per (trial, gesture): did any occurrence go wrong, and which?

    Neither a timestamp nor an ordinal, so the question names no moment at all -- the
    answering model has to find it. That is the half of the task a timestamped
    question deletes.

    This is only well posed because the UVA labels are exhaustive rather than a list
    of errors: 3,226 gesture instances are explicitly scored not-error against 933
    errors, covering 95% of all instances. So for a given trial and gesture we know
    which occurrences are clean, a model naming the wrong one is measurably wrong, and
    a gesture with no errors at all becomes a valid question whose answer is "none" --
    the first negative examples in any of these batches, and a direct test of whether
    a model invents faults.

    The remaining 5% is why a gesture is skipped unless every one of its occurrences
    in that trial carries a label. Without that, "did any go wrong" has no defensible
    answer.
    """
    span_list = read_spans(root, trial)
    if not span_list:
        return []
    by_key = {(l["start_frame"], l["end_frame"]): l for l in labels
              if l["trial_id"] == trial}

    by_gesture: dict[str, list[tuple[int, int]]] = {}
    for s, e, g in span_list:
        by_gesture.setdefault(g, []).append((s, e))

    out: list[dict[str, Any]] = []
    for gesture, occurrences in by_gesture.items():
        occurrences.sort()
        if any((s, e) not in by_key for s, e in occurrences):
            continue                      # unlabelled occurrence: not answerable
        faults = []
        for i, (s, e) in enumerate(occurrences, start=1):
            types = sorted({l["error_type"] for l in labels
                            if l["trial_id"] == trial and l["start_frame"] == s
                            and l["end_frame"] == e and l["is_error"]
                            and l["error_type"] not in EXCLUDED_ERROR_TYPES})
            if types:
                faults.append({"index": i, "span": f"{timestamp(s)}-{timestamp(e)}",
                               "start_frame": s, "end_frame": e, "error_types": types})
        label = GESTURES.get(gesture, gesture)
        aspect = GESTURE_ASPECT.get(gesture, "technique")
        n = len(occurrences)
        if n < 2:
            continue                      # no localizing to do with one occurrence

        if faults:
            fact_lines = [
                f"- Occurrence {f['index']} of {n}: "
                f"{'; '.join(ERROR_QUESTIONS[t][0] for t in f['error_types'] if t in ERROR_QUESTIONS)}."
                for f in faults if any(t in ERROR_QUESTIONS for t in f["error_types"])]
            if not fact_lines:
                continue
            rest = n - len(fact_lines)
            fact_lines.append(
                "- Every occurrence was marked as an error." if rest == 0 else
                "- The other one was not marked as an error." if rest == 1 else
                f"- The other {rest} were not marked as errors.")
            # "Which" has to mean the ordinal, explicitly. One answer said "during two
            # of those three transfers" -- true, and unscoreable, because it never
            # says which two. Naming them is what lets a record be checked against
            # the stored occurrence indices.
            #
            # This now carries the whole requirement. The question used to end "If so,
            # which?" and no longer does, because that phrasing led the answer; the
            # demand for a position moved here, alongside the facts, which is where
            # everything the question withholds already lives.
            guidance = ("Name the occurrence by its position -- 'the second', 'the "
                        "first and third', 'all of them' -- and say what happened "
                        "there. A count on its own ('two of them') is not an answer. "
                        "Do not comment on the occurrences that were fine, and do not "
                        "mention the annotations or how the error was recorded. Where "
                        "you name a likely cause, mark it with the words "
                        f"\"{hedge_for(trial + gesture)}\" and no other hedge. "
                        f"{opening_for(trial + gesture, clean=False)}")
        else:
            fact_lines = [f"- None of the {n} occurrences was marked as an error."]
            # Deliberately narrow. "Say so plainly" alone left a vacuum the model
            # filled: one clean answer added "well-managed timing and stable
            # instrument control", neither of which anything recorded, and closed
            # with advice that rule 7 forbids. A question whose answer is "nothing"
            # needs to say what the answer may consist of, not only what it may not.
            # Every word here is chosen for what it will NOT seed. An earlier version
            # ended "you know only that no error was recorded", and both clean answers
            # came back citing "the annotation log" and "the recording annotations" --
            # the model reusing the vocabulary the instruction handed it, which is the
            # third time that has happened. So this version never says "recorded",
            # "annotation" or "log", and says outright that the trainee is not to be
            # told where the judgement came from.
            #
            # None of which worked, because the guidance was not where the problem
            # was. Both clean answers in the next batch still added a quality nothing
            # recorded -- "that consistent trajectory ... warrants preservation" and
            # "a steady execution, maintaining consistent tension" -- and the second
            # of those is nearly a quotation of the system prompt, whose rule 8 read
            # "say so plainly and name what to preserve". The guidance was forbidding
            # exactly what the system prompt was asking for, and a system prompt wins.
            # Rule 8 has been rewritten to forbid inventing a merit as firmly as rule
            # 3 forbids inventing a fault. The sentence limit moved into the same rule
            # because rule 1's "two or three sentences" was the other half of the
            # pressure: an answer with one thing to say was padding to reach a count.
            #
            # And the positive example that replaced it -- "'the four pulls went
            # cleanly'" -- turned out to be worse than the problem. Three of the seven
            # clean answers about G3, which is PUSHING the needle through the tissue,
            # came back as "the four pulls went cleanly": the model took the example's
            # noun along with its shape and described the wrong action. Sixth time a
            # phrase from an instruction has returned verbatim, and the first time it
            # made an answer factually wrong rather than merely repetitive. There is
            # now no positive example at all, only the rule that the question's own
            # words for the action are the ones to use.
            guidance = ("These all went fine. The whole answer is one sentence saying "
                        "so. A second sentence is a failed answer: there is nothing to "
                        "explain, nothing to single out and nothing to advise, and "
                        "naming any quality of the movement -- how steady, smooth, "
                        "consistent or well judged it was -- would be inventing it. "
                        "Write it as something the trainee did, not as something that "
                        "was or was not found -- 'no problem was identified' is wrong. "
                        "Name the action using the question's own words for it; do not "
                        "substitute a different action. "
                        f"{opening_for(trial + gesture, clean=True)}")

        out.append({
            "kind": "locate",
            "span": f"{timestamp(occurrences[0][0])}-{timestamp(occurrences[-1][1])}",
            # Two things are deliberately absent from this sentence.
            #
            # No total. Saying "4 separate times" hands over the count, which is part
            # of what the model should have to work out -- it would only need to judge
            # four known segments rather than find them. The count stays in the facts
            # the answerer is given, because that is the annotation it must report
            # against; it is absent only from the question.
            #
            # The fault clause stays, by review decision rather than by oversight.
            #
            # Review raised it -- "generally we shouldn't ask questions that lead to a
            # specific answer, i.e. during one or more of those" -- and it was replaced
            # with a neutral "How did those go?". The reviewer then confirmed "I think
            # this is ok for now" and asked for the original to be kept, so it is back.
            #
            # The objection was real and is recorded here so it is not rediscovered
            # from scratch: "one or more" presupposes the set is non-empty, which is
            # false for roughly three quarters of the questions this builder produces,
            # and it dictates the shape of the reply -- confirm or deny, then select a
            # subset. Against that, the explicit "If so, which?" is what made the
            # answers state their verdict plainly; with the neutral wording one clean
            # answer reported only that the action had happened and never that it went
            # fine. Both effects are attested. If this is revisited, that is the
            # trade-off to weigh, not a question of whether the wording is leading.
            # Plain character sum, where hedge_for() weights by position: the two
            # picks are taken from the same seed, and 8 and 6 share a factor, so
            # using one function for both would tie some templates to a subset of
            # the hedges.
            "question": LOCATE_ASKS[sum(map(ord, trial + gesture))
                                    % len(LOCATE_ASKS)].format(label=label),
            "facts": "\n".join(fact_lines),
            "guidance": guidance,
            "grounding": {"gesture": gesture, "occurrences": n,
                          "faults": faults,
                          "error_types": sorted({t for f in faults
                                                 for t in f["error_types"]}),
                          "source_files": sorted({by_key[(s, e)]["source_file"]
                                                  for s, e in occurrences})},
        })
    return out


def build_error_questions(task: str, trial: str, root: Path, meta: dict,
                          labels: list[dict[str, Any]], baselines: dict,
                          anchor: str = "timestamp") -> list[dict[str, Any]]:
    """One question per recorded error in this trial, keyed to its own span.

    Where a span carries two error types, both are named: 29 spans in the dataset do,
    and telling the trainee about one while silently holding back the other would
    make the question less true than the annotation it came from.
    """
    span_list = read_spans(root, trial)
    spans = {(s, e): g for s, e, g in span_list}
    if not spans:
        return []
    kin = read_kinematics(root, trial)

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for lab in labels:
        if lab["trial_id"] != trial or not lab["is_error"]:
            continue
        if lab["error_type"] in EXCLUDED_ERROR_TYPES:
            continue
        grouped.setdefault((lab["start_frame"], lab["end_frame"]), []).append(lab)

    out: list[dict[str, Any]] = []
    for (start, end), labs in sorted(grouped.items()):
        # Most explanatory error first; see ERROR_SPECIFICITY.
        labs.sort(key=lambda l: (ERROR_SPECIFICITY.index(l["error_type"])
                                 if l["error_type"] in ERROR_SPECIFICITY else 99))
        primary = labs[0]["error_type"]
        if primary not in ERROR_QUESTIONS:
            continue
        gesture = spans.get((start, end))
        if gesture is None:
            # Alignment is verified at 100% before generation, so this means the
            # label set and the transcriptions have diverged. Skip loudly rather
            # than attach an error to a span that does not exist.
            print(f"    !! {trial} {start}-{end}: no transcription span, skipping")
            continue
        described, ask = ERROR_QUESTIONS[primary]
        others = [l["error_type"] for l in labs[1:] if l["error_type"] in ERROR_QUESTIONS]

        grounding: dict[str, Any] = {
            "error_types": [l["error_type"] for l in labs],
            "source_files": sorted({l["source_file"] for l in labs}),
            "gesture": gesture, "start_frame": start, "end_frame": end,
        }
        # Stated plainly, with no "a reviewer watching the video recorded that" framing.
        # That phrasing was in the first version and the model echoed it: 11 of 12
        # rationales came back narrating "the reviewer's explicit note of ...", which
        # tells the trainee about the annotation process instead of about their hands.
        # The provenance belongs in the record's grounding field, not in the text.
        fact_lines = [f"- {described.capitalize()}."]
        if others:
            fact_lines.append(f"- {ERROR_QUESTIONS[others[0]][0].capitalize()}.")

        # The duration comparison is kept on the record but no longer shown to the
        # model. It is a leftover from the proxy design, where timing was the only
        # evidence there was; here the recorded error is the finding, and offering a
        # "took 14.0s against 3.2s" alongside it pulls the answer back toward the
        # generic timing-and-path language that grounding in real errors was meant to
        # get away from. It stays in grounding so the number is still recoverable.
        m = span_metrics(kin, start, end)
        base = baselines.get(gesture, {})
        if m.get("duration_s") and base.get("duration_s"):
            grounding.update(duration_s=round(m["duration_s"], 2),
                             expert_median_s=round(base["duration_s"], 2),
                             ratio=round(m["duration_s"] / base["duration_s"], 2))
        span_label = f"{timestamp(start)}-{timestamp(end)}"
        out.append({
            "kind": "error",
            "span": span_label,
            "question": scoped_question(
                span_label, gesture, f"{trial}{start}", anchor,
                *occurrence_index(span_list, start, gesture)),
            "facts": "\n".join(fact_lines),
            # The per-type steer that broke the boilerplate stays, but as guidance to
            # the answerer rather than words in the trainee's mouth -- no trainee asks
            # to be answered "in terms of the hold on the needle". Kept out of the
            # facts list too, since that list is headed "what the annotations record"
            # and an instruction is not an annotation.
            "guidance": (f"{ask} Where you name a likely cause, mark it with the words "
                         f"\"{hedge_for(f'{trial}{start}{gesture}')}\" and no other "
                         f"hedge."),
            "grounding": grounding,
        })
    return out


def build_questions(task: str, trial: str, root: Path, meta: dict,
                    events: list[dict], kinds: tuple[str, ...] = VALIDATED_KINDS
                    ) -> list[dict[str, Any]]:
    """One question per notable moment, each stating the fact it rests on.

    kinds restricts output to question types whose measure is validated; see
    VALIDATED_KINDS. Pass a wider tuple only to measure, not to generate output.
    """
    spans = read_spans(root, trial)
    if not spans:
        return []
    out: list[dict[str, Any]] = []

    # --- localized duration outliers, worst first
    #
    # A span already covered by a combined question is skipped: slow spans and
    # far-travelling spans are largely the same spans, because a long reach is a
    # far-travelling reach. In the 8-4 batch that produced 5 pairs of near-identical
    # records asking about one moment twice.
    slow = sorted([e for e in events if e["kind"] == "slow"],
                  key=lambda e: -e["evidence"]["ratio"])
    wandering = sorted([e for e in events if e["kind"] == "wandering"],
                       key=lambda e: -e["evidence"]["ratio"])
    wander_by_span = {e["span"]: e for e in wandering}
    combined_spans: set[str] = set()

    for ev in slow[:2]:
        d = dict(ev["evidence"])
        also = wander_by_span.get(ev["span"])
        facts = [f"- The step took {d['duration_s']:.1f} seconds. The strongest recorded "
                 f"attempts at the same action take about {d['expert_median_s']:.1f} seconds."]
        if also:
            # One record carrying both measurements beats two carrying one each.
            combined_spans.add(ev["span"])
            w = also["evidence"]
            d["path_ratio"] = w["ratio"]
            d["side"] = w["side"]
            facts.append(f"- The {w['side']} instrument also covered {w['ratio']:.1f} times "
                         f"the distance it covers in those attempts.")
        out.append({"kind": "outlier", "span": ev["span"],
                    "question": scoped_question(ev["span"], ev["gesture"],
                                                 f"{trial}{ev['start_frame']}"),
                    "facts": "\n".join(facts),
                    "guidance": ("Describe it in terms of timing alone: where in the step "
                                 "the extra seconds most likely went."),
                    "grounding": d})

    # --- excess instrument travel, only where it is not already covered above
    for ev in [e for e in wandering if e["span"] not in combined_spans][:1]:
        d = ev["evidence"]
        out.append({
            "kind": "wandering",
            "span": ev["span"],
            "question": scoped_question(ev["span"], ev["gesture"],
                                         f"{trial}{ev['start_frame']}"),
            "facts": (f"- The {d['side']} instrument covered {d['ratio']:.1f} times the "
                      f"distance it covers in the strongest recorded attempts at this action."),
            "guidance": ("Describe it in terms of the path taken, not the timing: what "
                         "shape of movement produces that much extra distance."),
            "grounding": d,
        })

    # --- a gesture repeated more often than the task itself requires
    #
    # Not a raw count. Suturing_C004 scores 30 of 30 and still "positions the
    # needle" four times, because a four-stitch task needs four positionings --
    # asking about that measures the task's structure, not the trainee. So the
    # count is compared against the median count for the same gesture in the
    # strongest trials, and only a genuine excess becomes a question.
    #
    # Excluded by default: max gesture repeats correlates +0.04 with grs_total, so
    # repeating a step more often than peers says nothing about how the trial was
    # graded. Guarded rather than filtered at the end because this block reads every
    # peer trial's spans, which is the slowest thing in the function.
    counts = Counter(g for _, _, g in spans)
    peer_counts: dict[str, list[int]] = {}
    for other in (reference_peers(meta, trial) if "repetition" in kinds else []):
        other_spans = read_spans(root, other)
        if not other_spans:
            continue
        oc = Counter(g for _, _, g in other_spans)
        for g in counts:
            peer_counts.setdefault(g, []).append(oc.get(g, 0))
    excess = []
    for gesture, n in counts.items():
        peers_g = peer_counts.get(gesture, [])
        if len(peers_g) < 4:
            continue
        med = statistics.median(peers_g)
        if med >= 1 and n >= med + 2 and n >= 1.5 * med:
            excess.append((n - med, gesture, n, med))
    if excess:
        _, gesture, n, med = max(excess)
        occurrences = [timestamp(s) for s, _, g in spans if g == gesture]
        out.append({
            "kind": "repetition",
            "span": f"{timestamp(spans[0][0])}-{timestamp(spans[-1][1])}",
            "question": (
                f"During this recording the trainee returned to {GESTURES.get(gesture, gesture)} "
                f"{n} separate times, starting at {', '.join(occurrences[:4])}, where the "
                f"strongest recorded attempts at this task need it about {med:.0f} "
                f"{'time' if med == 1 else 'times'}. "
                f"Answer in terms of the number of attempts: what has to be true of an "
                f"attempt for it to be the last one, and what would let the trainee finish "
                f"in fewer?"),
            "grounding": {"gesture": gesture, "occurrences": n,
                          "peer_median": med, "starts": occurrences},
        })

    # --- closed choice on motion economy, the C7 pattern
    #
    # Excluded by default, and the most surprising of the three: this was the
    # best-verifying question shape, because a three-way choice is either right or
    # wrong. But gesture-segment count correlates +0.05 with grs_total, so calling
    # it "economy of motion" asserts a link to skill that the data does not support.
    # A cleanly verifiable question about the wrong quantity is still the wrong
    # question.
    peers = ([len(read_spans(root, t)) for t in meta if t != trial and read_spans(root, t)]
             if "economy" in kinds else [])
    if peers:
        median = statistics.median(peers)
        out.append({
            "kind": "economy",
            "span": f"{timestamp(spans[0][0])}-{timestamp(spans[-1][1])}",
            "question": (
                f"Completing this task took the trainee {len(spans)} separate gesture "
                f"segments, where the median across other recorded attempts at this task is "
                f"{median:.0f}. Is their economy of motion above, at, or below the typical "
                f"level for this task? Reply with one of those three words, then exactly one "
                f"sentence of justification, and nothing else. Do not add advice."),
            "grounding": {"segments": len(spans), "task_median": median},
        })

    # --- something done well, so the set is not only faults
    #
    # Excluded by default. Duration floors were added to localize_events.py to stop
    # fragmented spans reading as efficient, and they did remove the artefacts --
    # events fell from 220 to 138 across the dataset -- but rho only moved +0.03 to
    # +0.05. The detector finds nothing. Leaving the whole set fault-only is a real
    # cost, since a coaching dataset that only ever criticises teaches a coach that
    # only criticises; it is still better than 10 records claiming a trainee scoring
    # 7 of 30 has established sound technique.
    good = sorted([e for e in events if e["kind"] == "efficient"],
                  key=lambda e: e["evidence"]["ratio"])[:1] if "clean" in kinds else []
    for ev in good:
        d = ev["evidence"]
        out.append({
            "kind": "clean",
            "span": ev["span"],
            "question": (
                f"Between {ev['span']} the trainee completed {ev['gesture_label']} in "
                f"{d['duration_s']:.1f} seconds, against the {d['expert_median_s']:.1f} "
                f"seconds the strongest recorded attempts take. Answer in terms of "
                f"sequencing: what does completing this step in one pass without returning "
                f"to it suggest the trainee planned, and what should they keep doing as the "
                f"task gets harder?"),
            "grounding": d,
        })
    # Belt and braces: the guards above skip the expensive work, this guarantees
    # nothing unvalidated reaches a record even if a guard is missed later.
    return [q for q in out if q["kind"] in kinds]


def generate_ollama(host: str, model: str, system_prompt: str, question: str,
                    facts: str, guidance: str, num_ctx: int, max_new_tokens: int,
                    temperature: float) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": USER_PROMPT.format(question=question, facts=facts,
                                                       guidance=guidance)},
        ],
        "stream": False,
        "think": True,
        "options": {"temperature": temperature, "num_predict": max_new_tokens, "num_ctx": num_ctx},
    }
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    for attempt in range(TRANSPORT_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                result = json.loads(response.read())
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == TRANSPORT_RETRIES - 1:
                raise
            wait = TRANSPORT_BACKOFF_SECONDS * (attempt + 1)
            print(f"    transport error ({exc}); retrying in {wait}s")
            import time
            time.sleep(wait)
    msg = result["message"]
    thinking, content = msg.get("thinking") or "", msg.get("content") or ""
    return f"<think>{thinking}</think>{content}" if thinking else content


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jigsaws-root", default=None,
                    help="default: the shared cluster mount if present, else the "
                         "local clone under outputs/")
    ap.add_argument("--system-prompt", default="Prompts_And_Pipeline/system-prompt-events.md")
    ap.add_argument("--trials", default=None, help="comma separated, e.g. Suturing_C004,Knot_Tying_G004")
    ap.add_argument("--task", default=None)
    ap.add_argument("--trial", default=None)
    ap.add_argument("--backend", choices=["ollama", "transformers"], default="ollama",
                    help="ollama over HTTP (local, quantised) or transformers "
                         "in-process (cluster, full precision)")
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--ollama-model", default="qwen3.6-35b-a3b-iq4xs")
    ap.add_argument("--ollama-num-ctx", type=int, default=12288)
    ap.add_argument("--model-id", default="Qwen/Qwen3.6-35B-A3B",
                    help="transformers backend only")
    ap.add_argument("--device-map", default="auto", help="transformers backend only")
    ap.add_argument("--max-new-tokens", type=int, default=9000)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--questions-only", action="store_true",
                    help="print the questions and exit, without calling the model")
    # nargs="?" so the flag works bare: --error-labels switches to error-grounded
    # questions and finds the labels itself, on either machine. A path still wins if
    # one is given. The flag stays required to enter that mode -- the proxy questions
    # remain the default, because they are the only ones that cover Knot_Tying.
    ap.add_argument("--error-labels", nargs="?", const="auto", default=None,
                    help="build questions from the UVA-DSA recorded errors rather than "
                         "from motion proxies. Bare flag finds the labels; a path "
                         "overrides. Refuses to run unless every label's frame bounds "
                         "match a transcription span exactly.")
    ap.add_argument("--anchor", choices=["timestamp", "ordinal", "none"],
                    default="timestamp",
                    help="how the question names the moment. 'ordinal' drops the clock "
                         "and says which occurrence of the gesture it was, so the model "
                         "has to localize it rather than being handed the span.")
    ap.add_argument("--resume", action="store_true",
                    help="append to an existing qa_records.jsonl, skipping every "
                         "qa_id already recorded valid; for restarting a killed job")
    ap.add_argument("-o", "--output-dir", default="outputs/event_qa")
    args = ap.parse_args()
    jigsaws_root = resolve_jigsaws_root(args.jigsaws_root)
    if not jigsaws_root.is_dir():
        raise SystemExit(f"no JIGSAWS dataset at {jigsaws_root}; pass --jigsaws-root")
    print(f"jigsaws: {jigsaws_root}")

    wanted = []
    if args.trials:
        wanted = [t.strip() for t in args.trials.split(",") if t.strip()]
    elif args.trial:
        wanted = [args.trial]
    elif args.task:
        root = resolve_task_root(jigsaws_root, args.task)
        wanted = [t for t in read_meta(root, args.task)
                  if (root / "transcriptions" / f"{t}.txt").exists()]
    else:
        ap.error("give --trial, --trials or --task")

    system_prompt = extract_system_prompt(Path(args.system_prompt))
    print(f"system prompt: {len(system_prompt)} chars")

    # Error labels, and the precondition that makes them safe to use. The failure to
    # guard against is not a crash: it is attaching the wrong error to the wrong span,
    # which produces a batch that looks entirely healthy and is wrong throughout.
    labels: list[dict[str, Any]] = []
    if args.error_labels:
        from error_labels import (LABEL_ROOT_CANDIDATES, check_alignment, load_all,
                                  resolve_label_root)
        label_root = resolve_label_root(
            None if args.error_labels == "auto" else args.error_labels)
        if label_root is None or not label_root.is_dir():
            raise SystemExit(
                "no error labels found. Looked in:\n"
                + "".join(f"  {c}\n" for c in LABEL_ROOT_CANDIDATES)
                + "Clone them, or pass a path to --error-labels.")
        print(f"error labels: {label_root}")
        labels = load_all(label_root)
        if not labels:
            raise SystemExit(f"parsed 0 error labels from {label_root}")
        print(f"error labels: {len(labels)} rows")
        if check_alignment(labels, jigsaws_root) != 0:
            raise SystemExit(
                "refusing to generate: some error labels do not correspond exactly to a "
                "transcription gesture span, so an error could be attached to the wrong "
                "moment. Re-check the label set against the dataset before continuing.")
        usable = sum(1 for l in labels if l["is_error"]
                     and l["error_type"] not in EXCLUDED_ERROR_TYPES)
        print(f"  alignment verified; {usable} usable errors after excluding "
              f"{', '.join(EXCLUDED_ERROR_TYPES[:1])}")

    # Resolve every trial's questions before loading a 69 GB model, so a bad
    # --jigsaws-root or a missing kinematics directory fails in seconds rather
    # than after the load. Nothing here calls the model.
    plan: list[tuple[str, str, dict, list[dict[str, Any]]]] = []
    for trial in wanted:
        task = next((t for t in TASK_OF if trial.startswith(t)), None)
        if not task:
            print(f"  {trial}: cannot infer task from the trial id, skipping")
            continue
        root = resolve_task_root(jigsaws_root, task)
        meta = read_meta(root, task)
        baselines = build_baselines(root, task, meta, exclude=trial)
        if labels and args.anchor == "none":
            questions = build_localization_questions(task, trial, root, labels)
        elif labels:
            questions = build_error_questions(task, trial, root, meta, labels,
                                              baselines, args.anchor)
        else:
            events = events_for_trial(root, task, trial, meta, baselines)
            questions = build_questions(task, trial, root, meta, events)
        info = meta.get(trial, {})
        print(f"{trial}  skill {info.get('skill_level')}  GRS {info.get('grs_total')}/30  "
              f"-> {len(questions)} questions")
        plan.append((task, trial, info, questions))
    total_questions = sum(len(q) for *_, q in plan)
    print(f"\n{len(plan)} trials, {total_questions} questions")

    model_label = args.ollama_model if args.backend == "ollama" else args.model_id
    processor = model = None
    if args.backend == "transformers" and not args.questions_only:
        processor, model = load_text_model(args.model_id, args.device_map)

    # Append and flush per record rather than buffering to the end. A cluster run
    # over all of JIGSAWS takes hours, and the earlier all-at-once write meant a
    # preemption discarded everything; it also made a running job look empty,
    # which was misdiagnosed once as the job having been killed.
    out_path = Path(args.output_dir) / "qa_records.jsonl"
    done: set[str] = set()
    if args.resume and out_path.exists():
        existing = [json.loads(l) for l in
                    out_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        keep = [r for r in existing if r.get("validation_status") == "valid"]
        done = {r["qa_id"] for r in keep}
        # Rejected records are retried, so their old lines have to go or the file
        # ends up with two entries under one qa_id -- one rejected, one valid --
        # and every downstream count is then wrong in a way nothing reports.
        # Rewrite via a temp file so an interruption cannot truncate finished work.
        if len(keep) != len(existing):
            tmp = out_path.with_name(out_path.name + ".tmp")
            tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep),
                           encoding="utf-8")
            tmp.replace(out_path)
            print(f"resuming: dropped {len(existing) - len(keep)} rejected record(s) "
                  f"to be retried")
        print(f"resuming: {len(done)} valid records already in {out_path}")
    if not args.questions_only:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    sink = out_path.open("a" if args.resume else "w", encoding="utf-8") \
        if not args.questions_only else None

    records: list[dict[str, Any]] = []
    for task, trial, info, questions in plan:
        print(f"\n{trial}  {len(questions)} questions")
        for q in questions:
            if args.questions_only:
                print(f"  [{q['kind']}] {q['question']}")
                for line in q["facts"].splitlines():
                    print(f"      {line}")
                print(f"      (guidance) {q['guidance']}")
                continue
            qa_id = f"event_{trial}_{q['kind']}_{q['span'].replace(':', '')}"
            if qa_id in done:
                print(f"  [{q['kind']:10s}] already done, skipping")
                continue
            attempts, errors = 0, []
            qa = None
            raw = ""
            while attempts <= args.max_retries:
                attempts += 1
                if args.backend == "ollama":
                    raw = generate_ollama(args.ollama_host, args.ollama_model, system_prompt,
                                          q["question"], q["facts"], q["guidance"],
                                          args.ollama_num_ctx,
                                          args.max_new_tokens, args.temperature)
                else:
                    raw = run_generation(processor, model, system_prompt,
                                         USER_PROMPT.format(question=q["question"],
                                                            facts=q["facts"],
                                                            guidance=q["guidance"]),
                                         args.max_new_tokens, args.temperature)
                try:
                    parsed = parse_json_payload(raw)
                    # The prompt asks for a single-element array, and the model
                    # sometimes returns the bare object instead. Rejecting that
                    # discarded 2 of 19 records whose content was complete and
                    # correct, after four attempts each. The wrapper is not the
                    # data, so accept either shape.
                    if isinstance(parsed, dict):
                        parsed = [parsed]
                    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                        qa = parsed[0]
                        break
                    errors.append(f"schema_mismatch: got {type(parsed).__name__}")
                except Exception as exc:  # noqa: BLE001 - record and retry
                    errors.append(f"{type(exc).__name__}: {exc}")
                if attempts > args.max_retries:
                    break
            status = "valid" if qa else "rejected"
            print(f"  [{q['kind']:10s}] {status}"
                  + (f"  ({len(str(qa.get('answer', '')).split())} words)" if qa else ""))
            record = {
                "qa_id": qa_id,
                "dataset": "JIGSAWS", "task": task, "trial_id": trial,
                "question_kind": q["kind"], "span": q["span"],
                "generation_mode": "annotation_only_event_grounded",
                "grounding": q["grounding"],
                "facts_shown_to_model": q["facts"],
                "answer_guidance": q["guidance"],
                "source_annotation": {"skill_level": info.get("skill_level"),
                                      "grs_total": info.get("grs_total")},
                "qa": [qa] if qa else None,
                "validation_status": status,
                "validation_error": errors[-1] if errors and not qa else None,
                "model": {"model_id": model_label, "backend": args.backend,
                          "attempts": attempts,
                          "raw_output": raw if not qa else None,
                          "system_prompt_path": args.system_prompt},
            }
            records.append(record)
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
            sink.flush()

    if sink:
        sink.close()
        valid = sum(1 for r in records if r["validation_status"] == "valid")
        print(f"\nwrote {out_path}: {len(records)} new records, {valid} valid"
              + (f" (+{len(done)} kept from an earlier run)" if done else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
