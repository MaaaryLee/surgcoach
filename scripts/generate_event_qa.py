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
from localize_events import (FPS, GESTURES, SLOW_RATIO, build_baselines,
                             events_for_trial, read_kinematics, read_meta, read_spans,
                             reference_trials, resolve_jigsaws_root, resolve_task_root,
                             span_metrics, timestamp)
from run_annotation_qa_jigsaws import (extract_system_prompt, load_text_model,
                                       parse_json_payload, run_generation,
                                       run_generation_llama, verify_llama_server)

# Defined here rather than imported, after the template B branch removed both from
# run_annotation_qa_jigsaws.py. The merge was textually clean -- no file was touched
# by both sides -- and this module then failed at import, which is the failure mode a
# conflict-free merge hides: git compares text, not names.
#
# Kept local rather than restored upstream on purpose. That module is being actively
# reshaped by someone else's pipeline, so reaching into it for two integers is the
# actual defect; the four functions above are its public surface and stable, these
# were never more than constants that happened to live next door.
TRANSPORT_RETRIES = 4
TRANSPORT_BACKOFF_SECONDS = 5

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
    "The answer field carries ONLY what the annotations above state. Every clause "
    "in it must trace to one of those lines. Why it happened goes in the separate "
    "inferred_cause field and nowhere else.\n\n"
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
CARDINAL_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight",
                  "nine", "ten", "eleven", "twelve")


def number_word(n: int) -> str:
    """Spelled out where English spells it out, digits beyond that.

    The facts are read by a model that copies their wording into its answer, so they
    are written as prose rather than as a data structure -- "the second of the four",
    not "occurrence 2 of 4".
    """
    return CARDINAL_WORDS[n - 1] if 1 <= n <= len(CARDINAL_WORDS) else str(n)

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
# Two rounds of review reshaped these after the first six, and both rounds are worth
# recording because they cut against instincts that produced the first set.
#
# Readability beats structural distance. The first six were built so that each was a
# different sentence TYPE, and two of them -- "Which of the several times the trainee
# was X in this recording, if any, involved a mistake?" and "Of the occasions ...,
# were any faulty, and which?" -- were reported as hard to parse on a first read. They
# were: both centre-embed, holding a long subject open before the verb arrives, and
# both bury the yes/no gate mid-clause. That is what packing recurrence, the gate and
# the "which" into a single sentence costs. Every template is now two sentences, the
# first naming the action and the second asking plainly; the variety lives in the
# opening rather than in syntactic contortion.
#
# Do not assert how many times it happened. Five of the six used to open "multiple
# times", "several times", "more than once". Always true -- the builder skips any
# gesture occurring fewer than twice -- but it hands over for free a fact the model is
# supposed to establish, and "which occasion?" already implies there are occasions to
# choose between. Only the first still claims a count, because that is the wording the
# reviewer approved before this point was raised.
#
# "Faulty" is gone with the template that carried it. It is not the annotations'
# word -- they say "went wrong" and "went as intended" -- and it had already reached
# three of thirty-eight answers, including "all of them were faulty". Any word
# introduced here becomes the answers' vocabulary, which by now is a rule rather than
# a surprise; these six borrow their register from the facts.
LOCATE_ASKS = (
    # statement, yes/no, elliptical wh. The wording review approved, unchanged.
    "The trainee was {label} multiple times in this recording. Did they make a "
    "mistake during one or more of those? If so, which?",
    # statement, then a single compound question
    "In this recording the trainee was {label}. Did anything go wrong on any "
    "occasion, and which?",
    # a single existential question
    "Was there a mistake at any point where the trainee was {label} in this "
    "recording? If so, which occasion?",
    # imperative opening, embedded interrogative
    "Go through each time the trainee was {label} in this recording. Report whether "
    "a mistake happened in any of them, and which.",
    # imperative opening, then a question
    "Assess each time the trainee was {label} in this recording. Did a mistake "
    "happen on any of them, and if so which?",
    # imperative opening, wh closer
    "Look at every occasion where the trainee was {label}. Which of them, if any, "
    "went wrong?",
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
    "Open by naming the action, using the same verb the question uses, before saying "
    "which occurrence went wrong.",
    "Open with a phrase that places the moment in the sequence, before the subject.",
    "Do not use the word \"trainee\" anywhere in the answer; refer to the occurrence "
    "or the action instead.",
)
CLEAN_OPENINGS = (
    # "In the question's own words" is gone from both banks. It was added to stop a
    # pushing gesture being described as pulling, and it worked -- but it also made
    # the gesture's full label the subject of the sentence, which is where "Pushing
    # the needle through the tissue completed without error" comes from. Naming the
    # verb is what fixed the wrong-action bug; naming the whole label was never
    # needed, and it is what makes the answers read like form fields.
    "Open by naming the action, using the same verb the question uses.",
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
    # "The step needed several attempts before it succeeded" was the wording, and
    # three of five Multiple-Attempts answers in job 29719 borrowed "the step" --
    # "On the first attempt, the step required several passes before succeeding",
    # which would read identically for any gesture in the dataset. Zero of five did
    # so in the batch before it; the newer questions restate the action less, so the
    # facts' phrasing won instead.
    #
    # The other two descriptions name the needle and so carry their own subject. This
    # one has to describe any gesture, so it cannot name one -- the fix is to give it
    # no noun to borrow at all, which leaves the model to supply the action itself.
    "Multiple Attempts": (
        "needed several attempts before succeeding",
        "Describe it in terms of the attempts: what made the earlier ones fail where "
        "the last one worked."),
    "Needle Drop": (
        "the needle came out of the instrument's grasp",
        "Describe it in terms of the hold on the needle: what let it escape at this "
        "point in the step."),
    # "Presented at the wrong orientation for the step" was the wording here, and it
    # appeared verbatim -- seven and eight words at a stretch -- in half the answers
    # of a batch. "For the step" is dataset vocabulary that no attending says out
    # loud, so every answer inherited it. Same meaning, plainer words.
    "Needle Orientation": (
        "the needle was held at the wrong angle for the pass",
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
            # Written as "The second of the four" rather than "Occurrence 2 of 4".
            #
            # The guidance already asks for an ordinal -- "the second", "the first and
            # third" -- but the facts sit closer to the content than the guidance
            # does, and the facts won: an answer came back reading "Occurrence 2 of 4
            # and occurrence 3 of 4 were executed with the needle presented at the
            # wrong orientation", which is correct, scoreable, and reads like a
            # database row rather than an assessment.
            #
            # Eighth instance of the model copying the wording it was handed. The
            # pattern is settled enough to state as a rule: anything written in the
            # facts will appear in an answer, so the facts have to be written the way
            # an answer should read, not the way a data structure should print.
            fact_lines = [
                f"- The {ORDINALS[f['index'] - 1]} of the {number_word(n)}: "
                f"{'; '.join(ERROR_QUESTIONS[t][0] for t in f['error_types'] if t in ERROR_QUESTIONS)}."
                if f["index"] <= len(ORDINALS) else
                f"- Occurrence {f['index']} of {n}: "
                f"{'; '.join(ERROR_QUESTIONS[t][0] for t in f['error_types'] if t in ERROR_QUESTIONS)}."
                for f in faults if any(t in ERROR_QUESTIONS for t in f["error_types"])]
            if not fact_lines:
                continue
            rest = n - len(fact_lines)
            # "Marked as an error" is gone from every one of these lines, and from the
            # clean case below. The wording was harmless while the answer was asked to
            # deliver a finding in its own words; it became a trap the moment the
            # prompt started asking for clauses "traceable to a line you were given".
            # Job 29577 came back with "none was marked as an error", "none of those
            # transfers recorded a mistake" and "without a recorded mistake" -- the
            # model quoting the annotation vocabulary because it had just been told to
            # be faithful to it. Seventh time a phrase from an instruction has
            # returned verbatim, and the first caused by asking for fidelity.
            fact_lines.append(
                "- All of them went wrong." if rest == 0 else
                "- The other one went as intended." if rest == 1 else
                f"- The other {number_word(rest)} went as intended.")
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
                        "mention the annotations or how the error was recorded. The "
                        "answer says what happened and stops -- no cause, no "
                        "mechanism, nothing about why. Put the likely cause in "
                        "inferred_cause instead, as one sentence beginning with the "
                        # Capitalized at the point of use. The hedges are stored
                        # lowercase because they used to appear mid-sentence inside
                        # the answer; under the split schema they always start
                        # inferred_cause, and the model copied the stored casing
                        # exactly -- four of six causes in the first split batch
                        # began with a lowercase letter.
                        f"words \"{hedge_for(trial + gesture).capitalize()}\" and no "
                        "other hedge. "
                        f"{opening_for(trial + gesture, clean=False)}")
        else:
            # "Both", not "all two". These lines are copied into answers, so an
            # ungrammatical one becomes an ungrammatical answer.
            fact_lines = [f"- Both of them went as intended." if n == 2 else
                          f"- All {number_word(n)} of them went as intended."]
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
                        # Two answers named the action and stopped -- "the trainee
                        # pulled the suture with the left hand on all five attempts"
                        # -- which is true, and does not answer the question that was
                        # asked. Saying the action happened is not saying it went well.
                        "so, and it has to say the outcome and not only that the "
                        "action happened. A second sentence is a failed answer: "
                        "there is nothing to "
                        "explain, nothing to single out and nothing to advise, and "
                        "naming any quality of the movement -- how steady, smooth, "
                        "consistent or well judged it was -- would be inventing it. "
                        "Write it as something the trainee did, not as something that "
                        "was or was not found -- 'no problem was identified' is wrong. "
                        "Use the same verb the question uses for the action -- do not "
                        "substitute a different one -- but say it in your own words "
                        "rather than repeating the question's phrasing. Leave "
                        "inferred_cause as an "
                        "empty string: nothing went wrong, so there is nothing to "
                        "explain. "
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


# --vts: grounded long-video QA in the form VideoTreeSearch trains and evaluates on
# (arXiv 2607.16189). There a question is about a long video, its answer depends on
# one short interval, and the model has to find that interval as well as answer. The
# paper keeps a question only if it passes three filters, carried over here:
#
#   single clue     the ground truth is one contiguous segment.
#   long enough     the clue is long enough to be seen, and the recording is long
#                   enough around it that finding it is a search. The paper keeps
#                   clues spanning at most 20% of the video; a JIGSAWS gesture can
#                   also be a fraction of a second, which no frame sampler at 1 fps
#                   would catch, so there is a floor on the clue as well.
#   unique          exactly one stretch of the recording answers the question. Each
#                   kind below says how it gets there.
#
# The kinds, the wording and the answers follow the MultiBypass140 locate set
# (multibypass_locate_full, 10-20-2026, and system-prompt-multibypass-locate.md), so
# the datasets read as one: the answer reports the stretch and what the annotation
# records about it, and nothing else -- no judgement and no feedback. That set is
# about an operation; JIGSAWS records a bench-top task, so each kind has a
# counterpart rather than a copy:
#
#   phase          one throw: a stitch in Suturing, a pass through a ring in Needle
#                  Passing, a throw of the knot in Knot Tying. JIGSAWS has no phase
#                  labels, but every task is the same few gestures repeated per throw
#                  -- 33 of 39 Suturing trials have exactly four, the four the task
#                  asks for -- so a throw is cut from the transcription, at each
#                  needle transfer (G4) or each reach that starts a knot throw (G12).
#                  Unique by its ordinal, and only asked where the trial has the
#                  task's own number of throws.
#   longest        the throw that takes longest, by a clear margin over the next.
#   step           a gesture the surgeon performs once in the trial, as a
#                  MultiBypass step is.
#   step_in_phase  a gesture performed once within its throw: "the needle pass in
#                  the third stitch". Unique by the pair, as a step inside a
#                  MultiBypass phase is.
#   step_nth       any other gesture, by which occurrence it is: "the second time
#                  the surgeon orients the needle". The transcription is complete,
#                  so the count is exact.
#   event          an executional error that is the only one of its type in the
#                  trial, the counterpart of an adverse event.
#   event_nth      an error whose type recurs, by which occurrence it is: "the second
#                  needle drop", the counterpart of the MultiBypass first bleed.
#                  Both event kinds need every span that could carry the type to be
#                  labelled, so "the only one" and "the second" are claims the labels
#                  make. Suturing and Needle Passing only: Knot Tying has no error
#                  labels.
#   worst          the step that runs furthest over the time the same action takes in
#                  the strongest trials, the counterpart of the most serious bleed.
#                  The error labels carry no grade, so "most serious" has to be
#                  measured, and duration is the one measure here validated against
#                  assessed skill (rho -0.44, VALIDATED_KINDS). Unique only with a
#                  clear margin, as "the single most serious" is.
#   redo           the surgeon goes back to an action right after starting the next
#                  one (X, Y, X in the transcription): "goes back to positioning the
#                  needle after pushing it through". Unique as the only such return,
#                  or by its ordinal.
#
# Redo exists because the kinds above follow the task's fixed order, so a model can
# often place them without watching: a blind guess from the question text finds 83%
# of phases and 81% of once-only steps (check_locate_qa.py). A redo happens wherever
# it happens. Two other irregular kinds were tried and left out for yielding almost
# nothing: the longest pause with both instruments still lasts a median of 1.3 s,
# with only 3 trials reaching 3 s; and the one stitch where the needle goes through
# twice occurs in 3 trials, each time over 20% of the recording.
#
# Every stretch that passes is asked about, the gestures once each: a gesture span
# carrying an error is asked as the error, otherwise by the least it takes to name
# it -- once in the trial, once in its throw, or its ordinal. The two extremes may
# share a clip with another question, as a MultiBypass bleed can be both an event
# and the most serious bleed. An earlier version took one phase and one step per
# trial, as the MultiBypass set does, and stopped at 166 questions.
VTS_MIN_CLUE_S = 3.0
VTS_MAX_CLUE_FRACTION = 0.20
VTS_MIN_TRIAL_S = 60.0
# The slowest span is "the" worst only when the next slowest is at most this share
# of it. At the median trial the runner-up is three quarters of the worst, which is
# a near tie, not a single worst.
VTS_WORST_MARGIN = 0.8
VTS_KINDS = ("phase", "longest", "step", "step_in_phase", "step_nth", "event",
             "event_nth", "worst", "redo")

# The answering model gets the annotation and the question and nothing else. The
# rules live in the system prompt, as they do for the MultiBypass locate questions;
# USER_PROMPT above belongs to the event questions and asks for an inferred cause,
# which these answers must not contain.
VTS_USER_PROMPT = (
    "The annotation for this question:\n{facts}\n\n"
    "The question (copy it verbatim into the question field):\n{question}\n\n"
    "Return valid JSON only, following the system prompt schema.")

# Each gesture in the forms the MultiBypass step frames need. Its labels are nouns
# ("jejunojejunal stapling") that drop into any frame; the JIGSAWS ones are gerund
# phrases, which do not, so each is written out: the verb phrase after "does the
# surgeon", the same with the surgeon as its subject, and what follows "how" in the
# CholecT50 frame -- passive where that reads naturally, otherwise active.
GESTURE_FORMS = {
    "G1": ("reach for the needle with the right hand",
           "reaches for the needle with the right hand",
           "the surgeon reaches for the needle with the right hand"),
    "G2": ("position the needle", "positions the needle", "the needle is positioned"),
    "G3": ("push the needle through the tissue", "pushes the needle through the tissue",
           "the needle is pushed through the tissue"),
    "G4": ("transfer the needle from left to right",
           "transfers the needle from left to right",
           "the needle is transferred from left to right"),
    "G5": ("move to the centre with the needle in grip",
           "moves to the centre with the needle in grip",
           "the surgeon moves to the centre with the needle in grip"),
    "G6": ("pull the suture with the left hand", "pulls the suture with the left hand",
           "the suture is pulled with the left hand"),
    "G7": ("pull the suture with the right hand", "pulls the suture with the right hand",
           "the suture is pulled with the right hand"),
    "G8": ("orient the needle", "orients the needle", "the needle is oriented"),
    "G9": ("use the right hand to help tighten the suture",
           "uses the right hand to help tighten the suture",
           "the right hand helps tighten the suture"),
    "G10": ("loosen more suture", "loosens more suture", "more suture is loosened"),
    "G11": ("drop the suture and move to the end points",
            "drops the suture and moves to the end points",
            "the surgeon drops the suture and moves to the end points"),
    "G12": ("reach for the needle with the left hand",
            "reaches for the needle with the left hand",
            "the surgeon reaches for the needle with the left hand"),
    "G13": ("make a C loop around the right hand", "makes a C loop around the right hand",
            "a C loop is made around the right hand"),
    "G14": ("reach for the suture with the right hand",
            "reaches for the suture with the right hand",
            "the surgeon reaches for the suture with the right hand"),
    "G15": ("pull the suture with both hands", "pulls the suture with both hands",
            "the suture is pulled with both hands"),
}

# The MultiBypass "phase" frame, the one that asks only when ("When in this operation
# does the {X} phase take place?"). "Operation" becomes "recording" in every frame
# here: this is a bench-top exercise, and the MultiBypass set says "recording" itself
# in one of its step frames.
VTS_PHASE_ASK = "When in this recording does the {name} take place?"
VTS_THROW = {"Suturing": "stitch", "Needle_Passing": "pass through a ring",
             "Knot_Tying": "throw of the knot"}
# How many throws the task itself asks for. "The second stitch" only names the same
# stretch a viewer would count where the transcription splits into that many; a
# trial that splits into six has extra transfers in it, and its "sixth stitch" is
# not something anyone watching could find.
VTS_EXPECTED_THROWS = {"Suturing": 4, "Needle_Passing": 4, "Knot_Tying": 2}

# The three MultiBypass "step" frames, then the CholecT50 one ("A trainee wants to
# see how the cystic artery is clipped. Show them when the surgeon does it in this
# operation.").
VTS_STEP_ASKS = (
    "Where in this recording does the surgeon {base}? Give the time span.",
    "Show the trainee where in this recording the surgeon {third}.",
    "A trainee wants to review the surgeon {gerund}. When in this recording does it "
    "happen?",
    "A trainee wants to see how {how}. Show them when {doer} in this recording.",
)

# The two MultiBypass "event" frames, "When in this operation does a ... occur?" and
# "Where in this operation is there a ...? Show the trainee when it happens.", per
# error type, because no one noun phrase reads in both for all three. The second
# element is what the error was, for the annotation and the record.
VTS_EVENT = {
    "Needle Drop": (
        ("When in this recording does a needle drop occur?",
         "Where in this recording is there a needle drop? Show the trainee when it "
         "happens."),
        "Needle drop -- the needle came out of the instrument's grasp"),
    "Needle Orientation": (
        ("When in this recording does a needle orientation error occur?",
         "Where in this recording is there a needle orientation error? Show the "
         "trainee when it happens."),
        "Needle orientation error -- the needle was held at the wrong angle"),
    "Multiple Attempts": (
        ("When in this recording does a step take multiple attempts?",
         "Where in this recording is there a step that takes multiple attempts? Show "
         "the trainee when it happens."),
        "Multiple attempts -- the step needed several attempts before it succeeded"),
}

# The two MultiBypass "worst bleed" frames: "Where in this operation is the most
# serious bleeding, and how serious is it?" and "Show the trainee the most serious
# bleed in this operation."
VTS_WORST_ASKS = (
    "Where in this recording does the surgeon lose the most time on a single step, "
    "and how much?",
    "Show the trainee the step in this recording where the surgeon loses the most "
    "time.",
)

# Ordinal and within-throw forms of the step frames above, for gestures performed
# more than once. "First" and "last" read better than "fourth of four", but naming
# every occurrence by its number keeps one rule for all of them.
VTS_STEP_NTH_ASKS = (
    "Where in this recording does the surgeon {base} for the {nth} time? Give the "
    "time span.",
    "Show the trainee the {nth} time the surgeon {third} in this recording.",
    "A trainee wants to review the {nth} time the surgeon {third}. When in this "
    "recording does it happen?",
)
VTS_STEP_IN_PHASE_ASKS = (
    "During the {phase}, when does the surgeon {base}? Give the time span.",
    "Show the trainee where the surgeon {third} during the {phase} in this recording.",
    "A trainee wants to see how {how} in the {phase}. Show them when {doer} in this "
    "recording.",
)
# The event frames with an ordinal, per error type.
VTS_EVENT_NTH = {
    "Needle Drop": (
        "When in this recording does the {nth} needle drop occur?",
        "Where in this recording is the {nth} needle drop? Show the trainee when it "
        "happens."),
    "Needle Orientation": (
        "When in this recording does the {nth} needle orientation error occur?",
        "Where in this recording is the {nth} needle orientation error? Show the "
        "trainee when it happens."),
    "Multiple Attempts": (
        "When in this recording is the {nth} step that takes multiple attempts?",
        "Where in this recording is the {nth} step that takes multiple attempts? Show "
        "the trainee when it happens."),
}
# The event frames for a return to an action, and with an ordinal.
VTS_REDO_ASKS = (
    "When in this recording does the surgeon go back to {x} after {y}?",
    "Where in this recording does the surgeon return to {x} right after {y}? Show the "
    "trainee when it happens.",
)
VTS_REDO_NTH_ASKS = (
    "When in this recording does the surgeon go back to {x} after {y} for the {nth} "
    "time?",
    "Show the trainee the {nth} time the surgeon returns to {x} right after {y} in this "
    "recording.",
)
# The worst-bleed frames again, for the longest throw.
VTS_LONGEST_ASKS = (
    "Which {throw} in this recording takes the longest, and how long does it take?",
    "Show the trainee the longest {throw} in this recording.",
)


def vts_throws(task: str, spans: list[tuple[int, int, str]]
               ) -> list[list[tuple[int, int, str]]]:
    """The trial cut into throws: at each transfer (G4), or for Knot Tying at each G12.

    Suturing and Needle Passing open with picking the needle up (G1, G5, G8) before
    the first positioning (G2); that run-in is not a throw, so the first throw starts
    at G2. The transfer between two throws belongs to neither. Every task ends with
    G11, dropping the suture and moving to the end points, which is not a throw
    either.
    """
    out: list[list[tuple[int, int, str]]] = []
    cur: list[tuple[int, int, str]] = []
    started = task == "Knot_Tying"
    for span in spans:
        g = span[2]
        if g == "G11":
            break
        if not started:
            if g != "G2":
                continue
            started = True
        if (task == "Knot_Tying" and g == "G12") or g == "G4":
            if cur:
                out.append(cur)
            cur = [] if g == "G4" else [span]
            continue
        cur.append(span)
    if cur:
        out.append(cur)
    return out


def build_vts_questions(task: str, trial: str, root: Path,
                        labels: list[dict[str, Any]], baselines: dict,
                        min_clue_s: float = VTS_MIN_CLUE_S,
                        max_clue_fraction: float = VTS_MAX_CLUE_FRACTION,
                        min_trial_s: float = VTS_MIN_TRIAL_S,
                        kinds: tuple[str, ...] = VTS_KINDS,
                        funnel: Counter | None = None,
                        tally: Counter | None = None) -> list[dict[str, Any]]:
    """The locate questions for one trial, in the kinds VTS_KINDS lists.

    funnel, when given, counts why each candidate was dropped, so the cost of every
    threshold can be read off a run rather than guessed. tally, when given, is shared
    across trials so each bank's wordings are used in turn.
    """
    funnel = funnel if funnel is not None else Counter()
    tally = tally if tally is not None else Counter()
    span_list = read_spans(root, trial)
    if not span_list:
        return []
    # The transcription runs to the end of the last gesture, a few frames short of
    # the video. That understates the trial slightly, which makes the clue fraction
    # err high -- the conservative direction for a ceiling.
    trial_s = max(e for _, e, _ in span_list) / FPS
    if trial_s < min_trial_s:
        funnel["trial too short"] += 1
        return []

    # Which gestures each error type is labelled on at all in this task. A needle
    # drop is only scored on some gestures, so "the only needle drop" needs every
    # span of those gestures in this trial to carry a needle-drop label.
    task_rows = [l for l in labels if l["task"] == task]
    scored_on = {t: {l["gesture"] for l in task_rows if l["error_type"] == t}
                 for t in ERROR_QUESTIONS}
    rows: dict[tuple[int, int], dict[str, bool]] = {}
    for l in task_rows:
        if l["trial_id"] == trial and l["error_type"] in ERROR_QUESTIONS:
            rows.setdefault((l["start_frame"], l["end_frame"]), {})[
                l["error_type"]] = l["is_error"]

    def types_at(key: tuple[int, int]) -> list[str]:
        return sorted((t for t, err in rows.get(key, {}).items() if err),
                      key=ERROR_SPECIFICITY.index)

    def covered(t: str) -> bool:
        return all(t in rows.get((s, e), {}) for s, e, g in span_list
                   if g in scored_on[t])

    def usable(s: int, e: int) -> bool:
        clue_s = (e - s) / FPS
        if clue_s < min_clue_s:
            funnel["clue too short"] += 1
            return False
        if clue_s / trial_s > max_clue_fraction:
            funnel["clue too large a share of the trial"] += 1
            return False
        return True

    def quarter(s: int, e: int) -> int:
        return min(4, int(4 * (s + e) / 2 / FPS / trial_s) + 1)

    def pick(bank: tuple[str, ...], key: str) -> str:
        text = bank[tally[key] % len(bank)]
        tally[key] += 1
        return text

    def record(kind: str, s: int, e: int, question: str, recorded: str,
               fact_lines: list[str], extra: dict[str, Any]) -> dict[str, Any]:
        funnel[f"kept, {kind}"] += 1
        clue_s = (e - s) / FPS
        span = f"{timestamp(s)}-{timestamp(e)}"
        return {
            "kind": f"vts_{kind}",
            "span": span,
            "question": question,
            "recorded": f"{recorded}, {span}" + extra.pop("_after", ""),
            "facts": "\n".join(fact_lines + [f"- Time span: from {timestamp(s)} to "
                                             f"{timestamp(e)}"]),
            "guidance": "",
            "grounding": {
                "clue_start_frame": s, "clue_end_frame": e,
                "clue_s": round(clue_s, 2), "trial_s": round(trial_s, 2),
                "clue_fraction": round(clue_s / trial_s, 3),
                "quarter": quarter(s, e), **extra,
            },
        }

    label = lambda g: GESTURES.get(g, g)  # noqa: E731
    nth = lambda i: ORDINALS[i - 1]  # noqa: E731
    out: list[dict[str, Any]] = []
    asked: set[tuple[int, int]] = set()
    # Gestures the transcription labels twice back to back, as Knot_Tying_B001 does
    # with its C loop. On screen that is one action, so its count, its ordinals and
    # the clip itself are not what a viewer sees; no step or worst question uses it.
    doubled = {a[2] for a, b in zip(span_list, span_list[1:]) if a[2] == b[2]}

    throws = vts_throws(task, span_list)
    if throws and len(throws) != VTS_EXPECTED_THROWS[task]:
        funnel[f"phases: trial has {len(throws)} throws, not "
               f"{VTS_EXPECTED_THROWS[task]}"] += 1
        throws = []
    throw_name = lambda k: f"{nth(k)} {VTS_THROW[task]}"  # noqa: E731
    # The throw a gesture span sits in, where it is that gesture's only span there.
    alone_in: dict[tuple[int, int], int] = {}
    for k, throw in enumerate(throws, start=1):
        here = Counter(g for _, _, g in throw)
        alone_in.update({(s, e): k for s, e, g in throw if here[g] == 1})

    # events: every labelled error, as the only one of its type or by its ordinal.
    # A type is counted on every span that carries it. Counting it only where it is
    # a span's main type made four questions in the 10-24 batch wrong: a span with a
    # wrong angle AND multiple attempts was left out of the multiple-attempts count,
    # so "the only step that takes multiple attempts" had a second answer.
    by_type: dict[str, list[tuple[int, int]]] = {}
    for s, e, _ in span_list:
        for t in types_at((s, e)):
            by_type.setdefault(t, []).append((s, e))
    for s, e, g in span_list:
        types = types_at((s, e))
        if not types:
            continue
        t = types[0]
        if not covered(t):
            funnel["event: error type not labelled on every span"] += 1
            continue
        n, i = len(by_type[t]), by_type[t].index((s, e)) + 1
        kind = "event" if n == 1 else "event_nth"
        if kind not in kinds or i > len(ORDINALS) or not usable(s, e):
            continue
        asked.add((s, e))
        what = VTS_EVENT[t][1]
        lines = [f"- Error: {what}"]
        if n == 1:
            question, recorded = pick(VTS_EVENT[t][0], t), what
        else:
            question = pick(VTS_EVENT_NTH[t], f"{t} nth").format(nth=nth(i))
            recorded = f"{what} ({nth(i)} of {number_word(n)})"
            lines.append(f"- Which: the {nth(i)} of the {number_word(n)} times it "
                         f"happens in this recording")
        lines += [f"- Also recorded on the same stretch: {VTS_EVENT[x][1]}"
                  for x in types[1:]]
        out.append(record(kind, s, e, question, recorded,
                          lines + [f"- During: {label(g)}"],
                          {"gesture": g, "error_types": types, "occurrence": i,
                           "occurrences": n, "_after": f", during {label(g)}",
                           "source_files": sorted({
                               l["source_file"] for l in task_rows
                               if l["trial_id"] == trial and l["start_frame"] == s
                               and l["end_frame"] == e})}))

    # redo: the return to an action right after starting the next one
    redos: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for a, b, c in zip(span_list, span_list[1:], span_list[2:]):
        if a[2] == c[2] != b[2] and a[2] not in doubled:
            redos.setdefault((a[2], b[2]), []).append((c[0], c[1]))
    for (x, y), returns in redos.items():
        n = len(returns)
        for i, (s, e) in enumerate(returns, start=1):
            if ("redo" not in kinds or (s, e) in asked or i > len(ORDINALS)
                    or not usable(s, e)):
                continue
            asked.add((s, e))
            lines = [f"- Action: {label(x)}, done again right after {label(y)}"]
            recorded = f"Redo -- {label(x)} again after {label(y)}"
            if n == 1:
                question = pick(VTS_REDO_ASKS, "redo").format(x=label(x), y=label(y))
            else:
                question = pick(VTS_REDO_NTH_ASKS, "redo nth").format(
                    x=label(x), y=label(y), nth=nth(i))
                lines.append(f"- Which: the {nth(i)} of the {number_word(n)} times this "
                             f"happens in this recording")
                recorded += f" ({nth(i)} of {number_word(n)})"
            out.append(record("redo", s, e, question, recorded, lines,
                              {"gesture": x, "after": y, "occurrence": i,
                               "occurrences": n}))

    # steps: every other gesture span, named by the least that makes it unique
    for s, e, g in span_list:
        if (s, e) in asked or g not in GESTURE_FORMS:
            continue
        if g in doubled:
            funnel["step: gesture labelled twice back to back"] += 1
            continue
        i, n = occurrence_index(span_list, s, g)
        kind = ("step" if n == 1 else
                "step_in_phase" if (s, e) in alone_in else "step_nth")
        if kind not in kinds or i > len(ORDINALS) or not usable(s, e):
            continue
        asked.add((s, e))
        base, third, how = GESTURE_FORMS[g]
        doer = "it happens" if how.startswith("the surgeon") else "the surgeon does it"
        if kind == "step":
            question = pick(VTS_STEP_ASKS, kind).format(
                base=base, third=third, gerund=label(g), how=how, doer=doer)
            lines = [f"- Action: {label(g)}, the only time the surgeon does it in this "
                     f"recording"]
            recorded = label(g)
        elif kind == "step_in_phase":
            k = alone_in[(s, e)]
            question = pick(VTS_STEP_IN_PHASE_ASKS, kind).format(
                base=base, third=third, how=how, doer=doer, phase=throw_name(k))
            lines = [f"- Phase: the {throw_name(k)}",
                     f"- Action: {label(g)}, the only time the surgeon does it in that "
                     f"{VTS_THROW[task]}"]
            recorded = f"{label(g)}, in the {throw_name(k)}"
        else:
            question = pick(VTS_STEP_NTH_ASKS, kind).format(
                base=base, third=third, nth=nth(i))
            lines = [f"- Action: {label(g)}, the {nth(i)} of the {number_word(n)} times "
                     f"the surgeon does it in this recording"]
            recorded = f"{label(g)} ({nth(i)} of {number_word(n)})"
        out.append(record(kind, s, e, question, recorded, lines,
                          {"gesture": g, "occurrence": i, "occurrences": n}))

    # phases, and the longest of them by a clear margin
    spans_k = [(k, t[0][0], t[-1][1]) for k, t in enumerate(throws, start=1)]
    for k, s, e in spans_k:
        if len(throws[k - 1]) < 2:
            funnel["phase: a throw of one gesture"] += 1
            continue
        if "phase" in kinds and usable(s, e):
            out.append(record("phase", s, e, VTS_PHASE_ASK.format(name=throw_name(k)),
                              throw_name(k),
                              [f"- Phase: the {throw_name(k)}, of the "
                               f"{number_word(len(throws))} in this recording"],
                              {"throw": k, "throws": len(throws),
                               "gestures": [g for _, _, g in throws[k - 1]]}))
    ranked = sorted(spans_k, key=lambda x: x[2] - x[1], reverse=True)
    if "longest" in kinds and len(ranked) > 1:
        (k, s, e), (_, s2, e2) = ranked[0], ranked[1]
        took, runner = (e - s) / FPS, (e2 - s2) / FPS
        if runner > VTS_WORST_MARGIN * took:
            funnel["longest: no clear single longest"] += 1
        elif usable(s, e):
            out.append(record(
                "longest", s, e,
                pick(VTS_LONGEST_ASKS, "longest").format(throw=VTS_THROW[task]),
                f"Longest {VTS_THROW[task]} -- the {throw_name(k)}, {took:.1f} s "
                f"(next longest {runner:.1f} s)",
                [f"- Phase: the {throw_name(k)}, the longest of the "
                 f"{number_word(len(throws))} in this recording",
                 f"- Duration: {took:.1f} seconds; the next longest takes "
                 f"{runner:.1f} seconds"],
                {"throw": k, "throws": len(throws), "duration_s": round(took, 2),
                 "next_longest_s": round(runner, 2)}))

    # worst: the span furthest over the strongest trials' time, by a clear margin
    if "worst" in kinds:
        kin = read_kinematics(root, trial)
        timed = {}
        for s, e, g in span_list:
            m, base = span_metrics(kin, s, e), baselines.get(g, {})
            if m.get("duration_s") and base.get("duration_s"):
                timed[(s, e, g)] = (m["duration_s"], base["duration_s"])
        ratio = lambda k: timed[k][0] / timed[k][1]  # noqa: E731
        ranked_t = sorted(timed, key=ratio, reverse=True)
        if not ranked_t or ratio(ranked_t[0]) < SLOW_RATIO:
            funnel["worst: nothing ran slow"] += 1
        elif (len(ranked_t) > 1
              and ratio(ranked_t[1]) > VTS_WORST_MARGIN * ratio(ranked_t[0])):
            funnel["worst: no clear single worst"] += 1
        elif ranked_t[0][2] in doubled:
            funnel["worst: gesture labelled twice back to back"] += 1
        elif usable(ranked_t[0][0], ranked_t[0][1]):
            s, e, g = ranked_t[0]
            took, usual = timed[(s, e, g)]
            out.append(record(
                "worst", s, e, pick(VTS_WORST_ASKS, "worst"),
                f"Delay -- {took:.1f} s where the strongest recorded attempts take "
                f"about {usual:.1f} s",
                ["- The step that runs furthest over time in this recording",
                 f"- Action: {label(g)}",
                 f"- Duration: {took:.1f} seconds, where the strongest recorded "
                 f"attempts at the same action take about {usual:.1f} seconds"],
                {"gesture": g, "duration_s": round(took, 2),
                 "strongest_s": round(usual, 2), "ratio": round(took / usual, 2),
                 "runner_up_ratio": round(ratio(ranked_t[1]), 2)
                 if len(ranked_t) > 1 else None,
                 "_after": f", during {label(g)}"}))
    return sorted(out, key=lambda q: q["grounding"]["clue_start_frame"])


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
                    temperature: float, template: str = USER_PROMPT) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": template.format(question=question, facts=facts,
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
    ap.add_argument("--system-prompt", default=None,
                    help="default: system-prompt-jigsaws-locate.md with --vts, else "
                         "system-prompt-events.md")
    ap.add_argument("--trials", default=None, help="comma separated, e.g. Suturing_C004,Knot_Tying_G004")
    ap.add_argument("--task", default=None)
    ap.add_argument("--trial", default=None)
    ap.add_argument("--backend", choices=["ollama", "transformers", "llama"],
                    default="ollama",
                    help="ollama over HTTP (local, quantised), transformers "
                         "in-process (cluster, full precision), or a llama.cpp "
                         "server (cluster, quantised, one GPU)")
    ap.add_argument("--llama-host", default="http://127.0.0.1:8080",
                    help="llama backend only")
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
    ap.add_argument("--vts", action="store_true",
                    help="VideoTreeSearch-style grounded questions: one clue per "
                         "question, long enough to see, unique in the trial. Implies "
                         "--error-labels.")
    ap.add_argument("--keep", default=None,
                    help="--vts only: ask just the questions in this list, as written "
                         "by plan_locate_qa.py")
    ap.add_argument("--vts-kinds", default=",".join(VTS_KINDS),
                    help="comma separated subset of " + ", ".join(VTS_KINDS))
    ap.add_argument("--min-clue-s", type=float, default=VTS_MIN_CLUE_S)
    ap.add_argument("--max-clue-fraction", type=float, default=VTS_MAX_CLUE_FRACTION)
    ap.add_argument("--min-trial-s", type=float, default=VTS_MIN_TRIAL_S)
    ap.add_argument("--resume", action="store_true",
                    help="append to an existing qa_records.jsonl, skipping every "
                         "qa_id already recorded valid; for restarting a killed job")
    ap.add_argument("-o", "--output-dir", default="outputs/event_qa")
    args = ap.parse_args()
    if args.vts and not args.error_labels:
        args.error_labels = "auto"
    if args.system_prompt is None:
        args.system_prompt = ("Prompts_And_Pipeline/system-prompt-jigsaws-locate.md"
                              if args.vts
                              else "Prompts_And_Pipeline/system-prompt-events.md")
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
    funnel: Counter = Counter()
    tally: Counter = Counter()
    keep = None
    if args.keep:
        keep = {(k["trial_id"], k["kind"], k["span"], k["recorded"])
                for k in json.loads(Path(args.keep).read_text(encoding="utf-8"))}
        print(f"keep list: {len(keep)} questions from {args.keep}")
    template = VTS_USER_PROMPT if args.vts else USER_PROMPT
    for trial in wanted:
        task = next((t for t in TASK_OF if trial.startswith(t)), None)
        if not task:
            print(f"  {trial}: cannot infer task from the trial id, skipping")
            continue
        root = resolve_task_root(jigsaws_root, task)
        meta = read_meta(root, task)
        # Baselines are built only where they are used. Locate questions come from
        # the transcription and the error labels alone -- no timing, no distance --
        # but this was computed unconditionally, which meant reading every peer
        # trial's kinematics, the slowest step in the pipeline, and discarding the
        # result. It also made numpy a hard requirement for a mode that has no
        # numerical work in it: previewing the questions on a login node failed with
        # "numpy is required to read kinematics" for a code path that never reads
        # any.
        if args.vts:
            questions = build_vts_questions(
                task, trial, root, labels,
                build_baselines(root, task, meta, exclude=trial),
                args.min_clue_s, args.max_clue_fraction, args.min_trial_s,
                tuple(args.vts_kinds.split(",")), funnel, tally)
            if keep is not None:
                questions = [q for q in questions
                             if (trial, q["kind"], q["span"], q["recorded"]) in keep]
        elif labels and args.anchor == "none":
            questions = build_localization_questions(task, trial, root, labels)
        else:
            baselines = build_baselines(root, task, meta, exclude=trial)
            if labels:
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
    if args.vts:
        print("VTS filter, per candidate clue (trials for 'trial too short'):")
        for reason, n in funnel.most_common():
            print(f"  {n:4d}  {reason}")

    model_label = {"ollama": args.ollama_model,
                   "llama": f"llama.cpp at {args.llama_host}"}.get(args.backend,
                                                                  args.model_id)
    processor = model = None
    if args.backend == "transformers" and not args.questions_only:
        processor, model = load_text_model(args.model_id, args.device_map)
    if args.backend == "llama" and not args.questions_only:
        verify_llama_server(args.llama_host)

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
                if q["guidance"]:
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
                                          args.max_new_tokens, args.temperature,
                                          template)
                elif args.backend == "llama":
                    raw = run_generation_llama(args.llama_host, system_prompt,
                                               template.format(question=q["question"],
                                                               facts=q["facts"],
                                                               guidance=q["guidance"]),
                                               args.max_new_tokens, args.temperature)
                else:
                    raw = run_generation(processor, model, system_prompt,
                                         template.format(question=q["question"],
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
                "generation_mode": ("annotation_only_vts_grounded" if args.vts
                                    else "annotation_only_event_grounded"),
                # Which answer schema this record was generated under. "split" means
                # the answer carries annotation-supported statements only and any
                # cause lives in qa[0]["inferred_cause"]. Recorded rather than
                # inferred from the presence of the key, because the failure worth
                # catching is the model omitting that key on a fault record -- which
                # is indistinguishable from an older record unless the generator says
                # which schema it asked for.
                "answer_schema": "answer_rationale" if args.vts else "split",
                "grounding": q["grounding"],
                "question": q["question"],
                "recorded": q.get("recorded"),
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
