#!/usr/bin/env python3
"""Check event-grounded QA records.

check_template_d.py cannot be reused here. It decides whether a claim is grounded by
looking up a GRS subscore, and event records have none.

The original rule here was "an answer may assert only what its own question
asserted". That is now obsolete, and the reason is worth keeping: putting the
measurement in the question made the answer a paraphrase of it. The finding moved
into the prompt, so the answer must now assert MORE than its question -- the
opposite of the founding rule -- and be checked against the withheld annotation
instead.

Checks, roughly in the order they tend to matter:

  question loaded    too long, states measurements, or shares most of its content
                     words with its own answer. The question should be scoped by
                     span and aspect, not by handing over the finding.
  question leading   presupposes its own answer -- "did they make a mistake", "was
                     there anything wrong". A question that offers a fault as one
                     of two answers has already said which one is interesting.
  second person      "you executed the transfer". The trainee is described to a
                     supervisor, not addressed, so the whole batch is third person.
  answer silent      the inverse risk the above creates: an answer that never
                     reports what the annotations showed has told the trainee
                     nothing, and now nothing else in the record would reveal it.
  unmeasured         a mechanic nothing recorded -- wrist, grip force, tremor,
                     posture. "Consciously relaxing your wrist" is the motivating
                     case; it appeared in 6 of 19 answers in an early batch.
  narrates process   "a reviewer watching the video recorded a needle drop" tells
                     the trainee how the finding was obtained rather than what
                     happened. Barred in the answer; allowed in the rationale,
                     whose job is provenance.
  meta               a score, rubric or benchmark referred to as such, unless the
                     question used the same word first.
  patient            consequences needing a living patient. These are bench-top.
  unanchored         never refers to the span or the action, matched on stems so
                     "that left-hand pull" counts for "pulling suture".
  verbose            over MAX_SENTENCES or MAX_WORDS.
  boilerplate        batch-level: one phrase across too many answers. Per-record
                     checks are structurally blind to this, and it is the exact
                     complaint reviewers raised twice.

    python3 scripts/check_event_qa.py outputs/event_qa_scoped_8-4-2026
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

MAX_SENTENCES = 4      # the prompt asks for two or three; four is the tolerance
MAX_WORDS = 90

# --- checks for the question, not the answer -------------------------------------
#
# Added after review feedback from Yulu, which was about the questions rather than
# the answers, and which the checks above could not see at all:
#
#   "two different questions are getting the same answer"
#       -> check_batch, boilerplate and repeated-phrasing
#   "the question is so detailed that it's giving too much information and even
#    the answer"
#       -> question_too_long, question_states_the_finding, answer_overlap
#   "this is too aggressive to avoid being generic ... 'what advice to give in
#    needle handling' is enough"
#       -> question_has_span and question_has_aspect: scope it by span and aspect,
#          which is different from loading it with measurements
#   third person, not first
#       -> question_first_person
#
# The last one is the reason this section exists separately: every check written
# before it assumed the question was fixed and only the answer could be wrong.
#
# A second round, from Gedas, on the 8-sample locate batch:
#
#   "the answer is formulated in 2nd person 'You executed ...'. Maybe we could
#    rephrase it to be in 3rd person"
#       -> SECOND_PERSON, on the answer, and a batch check on repeated openers
#          because the obvious fix seeds "The trainee" as every first two words
#   "we shouldn't ask questions that lead to a specific answer, i.e. during one or
#    more of those"
#       -> LEADING, on every question kind rather than only the one he saw
#   "pretty rigid templates that don't have much variation within them"
#       -> not a check in this file yet. Measured before the fix: the locate form
#          used 1 sentence frame for 8 questions and the ordinal form 4 for 5.
MAX_QUESTION_WORDS = 35     # scoped questions run 20-26; the loaded ones ran 55-70
MAX_ANSWER_OVERLAP = 0.35   # share of answer content words already in the question
# Higher under the split schema, because the split makes the answer shorter and the
# measure is a ratio. Once the inferred cause moves to its own field, what remains is
# a terse statement of the annotation that must name the action -- and the question
# names the action too, so the shared words concentrate. Measured: job 29576 ran a
# median 40% overlap with the cause still inside the answer, and the split batch runs
# 36%, yet only the split batch flagged, because removing 13 words of cause removed
# 13 words of denominator. Penalising that is penalising the brevity the split exists
# to create -- the same error as flagging the one-sentence negatives, exempted above.
# 55% still catches an answer that is purely its own question rearranged.
MAX_ANSWER_OVERLAP_SPLIT = 0.55

TIMESTAMP = re.compile(r"\d+:\d+(?:\.\d+)?")
# "the third time the trainee was ...", or "at the point where ..." for a gesture that
# happens once. Either pins the moment without giving away the clock.
ORDINAL_ANCHOR = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|"
    r"twelfth)\b|\bat the point where\b|\bfind the point where\b",
    re.IGNORECASE,
)
FIRST_PERSON = re.compile(r"\b(I|I'm|I've|my|mine|me)\b")
SECOND_PERSON = re.compile(r"\b(you|your|yours|you're|you've|yourself)\b", re.IGNORECASE)
ASPECTS = ("needle handling", "suture handling", "technique", "instrument handling")

# A question that presupposes its own answer. Two distinct faults, both here:
#
#   raising a fault before anything is assessed -- "did they make a mistake", "was
#   there anything wrong" -- which nominates the interesting answer in advance and
#   is simply false for the roughly three quarters of locate questions whose spans
#   are all clean;
#
#   dictating the shape of the reply -- "one or more of those", "if so, which" --
#   which turns an open assessment into confirm-then-select.
#
# The requirement that a fault be named by position has not gone away. It lives in
# the answerer's guidance, alongside the facts, which is where everything the
# question deliberately withholds already lives.
# The one leading construction review has approved, excised before LEADING runs.
# Written as a pattern rather than an exact string so a paraphrase of it -- the
# diversity work asks for eight to twenty of them -- is exempt too. Deliberately
# narrow: it matches asking whether a mistake was made and which, and nothing else.
SANCTIONED_LEADING = re.compile(
    r"\b(?:did|do|does)\s+(?:they|the trainee)\s+(?:make|commit)\b[^.?]*\?"
    r"(?:\s*if\s+so,?\s*which\s*\??)?|"
    r"\bif\s+so,?\s*which\b\s*\??|"
    r"\b(?:was|were)\s+(?:a|any)\s+mistakes?\s+made\b[^.?]*\?|"
    r"\bone or more of (?:those|them)\b",
    re.IGNORECASE,
)

LEADING = re.compile(
    r"\bdid (?:they|the trainee|he|she)\b|\bwas there any(?:thing)?\b|"
    r"\bwere there any\b|\bany (?:mistakes?|errors?|problems?|faults?)\b|"
    r"\bone or more\b|\bif so,?\s*which\b|\bwhat went wrong\b|"
    r"\bwhich (?:one|ones|of those|of them) (?:went wrong|failed)\b|"
    r"\bdid (?:they|the trainee) (?:make|commit|drop|fail)\b",
    re.IGNORECASE,
)

# Words that carry meaning for the overlap measure; everything else is scaffolding.
OVERLAP_STOP = set(
    "the a an and or of to in on at for with that this is was were be been being it "
    "its you your they their there them he she we as by from not no but if then than "
    "so what which who whose how why when where all any both each few more most other "
    "some such only own same too very can will just should now do does did done have "
    "has had having are am would could trainee".split())


# Words a locate question uses to frame the ask rather than to name the action. They
# sit inside the span the label regex captures, so they have to come out again before
# the remainder counts as "the action the question named".
QUESTION_SCAFFOLD = set(
    "several occasions occasion times time multiple point points recording more once "
    "each every any them those mistake wrong happen happened".split())


def content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z']+", text.lower())
            if w not in OVERLAP_STOP and len(w) > 2]


def check_question(rec: dict, question: str, answer: str) -> list[str]:
    """Whether the question is scoped rather than loaded, and stays out of the answer."""
    issues: list[str] = []
    n_words = len(question.split())
    if n_words > MAX_QUESTION_WORDS:
        issues.append(f"question is {n_words} words (limit {MAX_QUESTION_WORDS}) -- "
                      f"scope it by span and aspect rather than by adding detail")

    m = FIRST_PERSON.search(question)
    if m:
        issues.append(f"question is first person ({m.group(0)!r}); the A-D templates "
                      f"and this one are third person")

    # Applied to every kind, not only to the locate form the review happened to see.
    # "Was there anything wrong with their needle handling?" was one of three scoped
    # templates and has the same defect.
    #
    # Except the one phrasing review has explicitly sanctioned. "Did they make a
    # mistake during one or more of those? If so, which?" was raised as leading,
    # replaced, and then reinstated on the reviewer's decision -- so flagging it would
    # report the approved design as a defect on every locate record, which is the
    # fastest way to teach someone to ignore this output. The check stays live for
    # everything else, so a NEW leading phrasing still gets caught.
    m = LEADING.search(SANCTIONED_LEADING.sub(" ", question))
    if m:
        issues.append(f"question leads the answer ({m.group(0)!r}): it nominates a "
                      f"fault, or the shape of the reply, before anything is assessed")

    # "locate" questions name no moment and no aspect ON PURPOSE -- the model is asked
    # to find which occurrence went wrong, so handing it either would remove the task.
    # Both checks below were written for question forms that do name a moment, and
    # applying them here flagged 16 issues on a batch whose real defects were three.
    # A check that fires on the design it was not written for teaches you to skim.
    if rec.get("question_kind") != "locate":
        # Localized by a clock OR by which occurrence of the gesture it was. The
        # second form exists so the model has to find the moment itself rather than
        # being handed it; requiring a timestamp would reject the harder question.
        if not (TIMESTAMP.search(question) or ORDINAL_ANCHOR.search(question)):
            issues.append("question names neither a timestamp nor which occurrence of "
                          "the action it was, so the moment it asks about is ambiguous")
        if not any(a in question.lower() for a in ASPECTS):
            issues.append(f"question names no aspect; one of {ASPECTS} keeps it from "
                          f"running in any direction")
    else:
        # What a locate question must not do instead: give away which occurrence, or
        # how many there are. Either turns finding the moment into judging a given one.
        m = ORDINAL_ANCHOR.search(question)
        if m:
            issues.append(f"locate question names the occurrence ({m.group(0)!r}), "
                          f"which is the thing the model is supposed to work out")
        counts = re.findall(r"\b(\d+|two|three|four|five|six|seven|eight)\b",
                            question, re.IGNORECASE)
        if counts:
            issues.append(f"locate question states how many occurrences there are "
                          f"({counts[0]!r}); say 'multiple times' instead")

    # A number that is not part of a timestamp is a measurement, and a measurement in
    # the question is the finding handed over before it is asked for.
    stripped = TIMESTAMP.sub(" ", question)
    leaked = re.findall(r"\d+(?:\.\d+)?", stripped)
    if leaked:
        issues.append(f"question states measurements {leaked[:3]} -- that is the answer, "
                      f"and it should sit in the prompt instead")

    # And the same thing measured rather than pattern-matched: if most of the answer's
    # content is already in the question, the answer is a paraphrase.
    #
    # Skipped when the correct answer is "nothing went wrong". A negative answer has to
    # reuse the question's own terms -- "did they make a mistake" is answered by "you
    # did not make a mistake" -- so overlap runs high no matter how good the answer is.
    # It flagged two correct one-sentence negatives at 44% and 38%, which is the metric
    # penalising the very brevity it exists to encourage.
    is_negative = (rec.get("question_kind") == "locate"
                   and not (rec.get("grounding") or {}).get("faults"))
    a_words = content_words(answer)
    if a_words and not is_negative:
        limit = (MAX_ANSWER_OVERLAP_SPLIT if rec.get("answer_schema") == "split"
                 else MAX_ANSWER_OVERLAP)
        q_words = set(content_words(question))
        share = sum(1 for w in a_words if w in q_words) / len(a_words)
        if share > limit:
            issues.append(f"{share:.0%} of the answer's content words are already in its "
                          f"own question (limit {limit:.0%})")
    return issues


# What the answer has to deliver, per record kind, now that the question withholds it.
# The inverse of this file's original rule: the answer used to be barred from going
# beyond its question, and now it fails if it does not.
FINDING_TERMS = {
    "Needle Drop": r"\b(drop\w*|escap\w*|slip\w*|came out|lost|fell|grasp|jaw|seat\w*)",
    # \w*align\w* rather than \balign\w*: "misaligned the needle on all of them" is a
    # plain report of a Needle Orientation error, and the word-boundary version missed
    # it because "align" sits inside "misaligned". Flagged a correct answer for never
    # reporting the finding it had just reported.
    "Needle Orientation": r"\b(orient\w*|angle|\w*align\w*|direction|perpendicular|"
                          r"curve|present\w*|face|facing)",
    # "Multiple tries", "multiple efforts", "needed multiple passes" -- three of the
    # four natural ways to say this missed, because the pattern listed the nouns one
    # batch had happened to use. The quantifier is the reliable part, not the noun.
    "Multiple Attempts": r"\b(attempt|repeat\w*|again|retr\w+|more than once|several|"
                         r"(?:multiple|repeated|numerous)\s+\w+|took \w+ tries|"
                         r"tries|efforts)",
}
MEASURE_TERMS = r"\b(second|seconds|longer|slower|time|distance|further|travel\w*)"

# Deliberately NOT FINDING_TERMS, which is a vocabulary set for the opposite question.
# FINDING_TERMS asks "does the answer report the recorded error", so it is broad on
# purpose -- "attempt", "again", "several" all count as reporting Multiple Attempts.
# Reusing it here flagged 15 of 38 answers in a clean batch, because "on the second
# attempt" is simply how you name an occurrence, not a claim that the step was
# attempted repeatedly.
#
# These patterns match a fault being ASSERTED, not the vocabulary surrounding it.
ASSERTS_FINDING = {
    "Needle Orientation": r"\b(?:misalign\w*|misdirect\w*|skewed|"
                          r"(?:wrong|incorrect|poor|bad)\s+(?:angle|orientation|"
                          r"alignment)|angled away|not (?:aligned|oriented))\b",
    "Needle Drop": r"\b(?:dropp?ed the needle|needle (?:dropped|slipped|fell)|"
                   r"came out of the (?:grasp|jaws)|lost (?:the )?(?:needle|grip)|"
                   r"escaped the (?:grasp|jaws))\b",
    "Multiple Attempts": r"\b(?:required|needed|took)\s+(?:several|multiple|repeated|"
                         r"a few|more than one)\s+(?:attempts?|tries|passes|goes)\b|"
                         r"\brepeated (?:attempts|tries)\b",
}


ORDINAL_WORD = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b",
    re.IGNORECASE)
# "All of them", "all seven occurrences", "each transfer", "every pass". The first
# version enumerated three|four|five|six and the nouns it had happened to see, and
# missed "all seven occurrences" and "Each transfer of the needle" on job 29577 --
# both correct answers to an all-fault record, both reported as failing to name which
# occurrence went wrong. Quantifier plus an optional number plus any noun, rather than
# a list of the combinations observed so far.
ALL_WORD = re.compile(
    r"\b(?:all|each|every|both)\s+"
    r"(?:of\s+(?:them|those|these)|"
    r"(?:the\s+)?(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\s+\w+|"
    r"\w+)", re.IGNORECASE)
# Widened when the locate question stopped asking "did they make a mistake". The old
# patterns only matched an answer echoing that phrasing back; against a neutral "how
# did those go?" the negative is stated in the answer's own words, so the ways of
# saying "nothing went wrong" have to be enumerated rather than mirrored.
# A cardinal used as an occurrence name rather than as a quantity: "occurrences two
# and three", "attempt four". The noun has to come first, which is what separates it
# from "two of them" -- a count that names nothing.
CARDINAL_ID = re.compile(
    r"\b(?:occurrences?|attempts?|instances?|passes|pass|times|transfers?|"
    r"repetitions?|occasions?)\s+"
    r"(?:\d+|two|three|four|five|six|seven|eight|nine|ten)"
    r"(?:\s*(?:,|and)\s*(?:\d+|two|three|four|five|six|seven|eight|nine|ten))*",
    re.IGNORECASE)

NO_FAULT = re.compile(r"\b(?:did not|didn't) make a mistake\b|"
                      r"\bno (?:error|mistake|fault|problem|issue)s?\b|"
                      r"\bwithout (?:committing|error|incident|issue|any|difficulty|"
                      r"a mistake|mistake|fault|problem)s?\b|"
                      r"\bzero errors?\b|\bnothing went wrong\b|"
                      r"\bnone of (?:them|those|these)\b|"
                      r"\b(?:all|both) (?:of )?(?:them|those|these)\b|"
                      r"\bwent (?:fine|well|smoothly|cleanly|as intended)\b|"
                      r"\bproceeded without\b|\bnothing (?:of note|notable|stood out)\b|"
                      # A verb of completion next to a word of success, in either
                      # order, rather than the exact pairs seen so far. This pattern
                      # has been widened four times -- "without fault", "completed
                      # successfully", "advanced exactly as intended", "met the
                      # intended outcome", "all succeeded" -- each time for a correct
                      # answer it had called defective. Enumerating observed phrasings
                      # catches last batch's wording, never this batch's.
                      r"\b(?:executed|completed|finished|passed|went|advanced|"
                      r"proceeded|met|performed)\b[^.]{0,25}"
                      r"\b(?:cleanly|successfully|smoothly|correctly|properly|fine|"
                      r"well|as intended|intended outcome|without)\b|"
                      r"\b(?:all |both |each )?(?:succeeded|were successful)\b|"
                      r"\b(?:exactly |just )?as intended\b|"
                      r"\bcleanly\b|\ball (?:four|three|two|of them) \w+ed\b",
                      re.IGNORECASE)


# Explaining or praising a clean span. An absent error label says one thing only:
# nobody recorded a fault. It does not say the tip was aligned, the force steady, or
# the timing good, and "you should preserve that steady execution" is advice that
# rule 7 bars anyway. Both slipped through a batch that the rest of these checks
# called clean, and negatives will be roughly three quarters of any full run, so this
# failure mode would dominate.
# The adjective and noun lists are separate and both open-ended, because the first
# version enumerated pairs and the model simply used a pair it did not contain:
# "consistent trajectory" passed while "consistent tension" would have been caught,
# on the same batch, for the same defect. Any quality word attached to any movement
# word is the failure, so match the shape rather than the vocabulary.
QUALITY = (r"steady|smooth|straight|stable|consistent|controlled|good|clean|"
           r"efficient|deliberate|confident|precise|fluid|even|sound|well[- ]judged")
MOVEMENT = (r"alignment|force|execution|control|technique|tension|pacing|timing|"
            r"motion|trajectory|path|pass|passes|approach|placement|positioning|"
            r"handling|sequence|rhythm|movement|grasp|traverse|advance")
UNEARNED_PRAISE = re.compile(
    r"\btypically follows from\b|\bthis is what happens when\b|\bfollows from\b|"
    r"\bbecause (?:you|they|the trainee)\b|\bthanks to\b|\bresult of\b|\breflects\b|"
    r"\bpoints to\b|\bindicat\w+\b|\bdemonstrat\w+\b|"
    rf"\b(?:{QUALITY})\s+(?:{MOVEMENT})\b",
    re.IGNORECASE)
# Noun forms as well as verbs. "Warrants preservation" is the same advice as "preserve
# that", and only the verb was matched -- so the one answer in the batch that gave
# advice on a clean span was the one this check missed.
KEEP_DOING = re.compile(
    r"\b(?:preserve|maintain|keep|continue|sustain)\b[^.]{0,40}\b"
    r"(?:that|this|it|throughout|going)\b|\b(?:you|they|the trainee) should\b|"
    r"\b(?:preservation|maintenance|continuation)\b|"
    r"\b(?:warrants?|worth|merits?)\s+\w+ing\b",
    re.IGNORECASE)


# The action the question asked about, against the action the answer describes.
#
# Written from a batch where three of seven clean answers to "the trainee was pushing
# the needle through the tissue" came back as "the four pulls went cleanly". The
# answers were fluent, correctly counted, correctly negative, and about the wrong
# gesture. Nothing else in this file could see it: the anchoring check passed on two
# of the three because they mention "needle" or "tissue", and every content check
# looks at what the answer asserts rather than at whether it is about the right thing.
#
# Only these six, and only where the question uses one of them. Gestures the map does
# not cover ("moving to the centre with the needle in grip") get no verb and are
# skipped -- an answer paraphrasing that as "the two reaches to the centre" is a
# reasonable rewording, not a substitution, and flagging it would be noise.
ACTION_VERBS = {
    "push": r"\bpush\w*", "pull": r"\bpull\w*", "transfer": r"\btransfer\w*",
    "position": r"\bposition\w*", "orient": r"\borient\w*", "reach": r"\breach\w*",
}


def check_action_matches(question: str, answer: str) -> list[str]:
    """Whether the answer describes the action the question asked about."""
    asked = [v for v, pat in ACTION_VERBS.items()
             if re.search(pat, question, re.IGNORECASE)]
    if not asked:
        return []
    if any(re.search(ACTION_VERBS[v], answer, re.IGNORECASE) for v in asked):
        return []
    # The right verb is absent. That alone is fine -- "the five needle passes went
    # cleanly" names no verb and is a good answer. It is only wrong when a DIFFERENT
    # action has been named in its place.
    wrong = [v for v, pat in ACTION_VERBS.items()
             if v not in asked and re.search(pat, answer, re.IGNORECASE)]
    if wrong:
        return [f"question asks about {asked[0]!r} but the answer describes "
                f"{wrong[0]!r} -- it is about the wrong action"]
    return []


def check_clean_answer(answer: str) -> list[str]:
    """A 'nothing went wrong' answer may report that, and nothing more."""
    issues = []
    m = UNEARNED_PRAISE.search(answer)
    if m:
        issues.append(f"clean span: answer explains or praises how it went well "
                      f"({m.group(0)!r}) -- only the absence of a recorded error is known")
    m = KEEP_DOING.search(answer)
    if m:
        issues.append(f"clean span: answer gives advice ({m.group(0)!r}); rule 7 asks "
                      f"for description only")
    return issues


# The answer/inferred_cause split, and the checks that keep it honest.
#
# Measured on job 29576, which passed every other check in this file: 31% of the
# words across 38 answers, and 54% of the words in the 13 fault answers, were the
# inferred cause -- exactly one hedged sentence per fault answer, with nothing in the
# record marking which sentence a reader could not verify. The clean answers were
# already at 0%, because their guidance forbids explaining.
#
# So the cause moved to its own field. The answer now carries only what the
# annotations state, and these checks exist because a prompt asking for that is not
# the same as getting it: the model has produced a cause in every fault answer for
# six consecutive batches, and habit is exactly what a check is for.
INFERENCE_MARKERS = re.compile(
    r"\bthe usual cause of this is\b|\bthis is what happens when\b|"
    r"\bthat pattern points to\b|\bconsistent with\b|\bfollows from\b|"
    r"\bmost likely\b|\bpoints to\b|\bsuggests?\b|\bindicat\w+\b|"
    r"\bbecause\b|\bdue to\b|\bresult\w*\s+(?:from|of)\b|\bcaused by\b|"
    r"\bowing to\b|\bstems? from\b|\battributable to\b|\bwhich allow\w*\b|"
    r"\brather than\b", re.IGNORECASE)


def check_answer_inference_free(rec: dict, qa: dict) -> list[str]:
    """The answer states annotations only; the cause lives in its own field."""
    issues: list[str] = []
    answer = str(qa.get("answer", ""))
    cause = str(qa.get("inferred_cause", "") or "")

    m = INFERENCE_MARKERS.search(answer)
    if m:
        issues.append(f"answer contains an inference ({m.group(0)!r}); it belongs in "
                      f"inferred_cause, which is the whole point of the split")

    has_faults = bool((rec.get("grounding") or {}).get("faults"))
    if has_faults and not cause.strip():
        issues.append("fault record has an empty inferred_cause -- the explanation "
                      "was dropped rather than moved")
    if not has_faults and cause.strip():
        issues.append(f"clean record has an inferred_cause ({cause[:48]!r}); nothing "
                      f"went wrong, so there is nothing to explain")
    if cause and len(sentences(cause)) > 2:
        issues.append(f"inferred_cause is {len(sentences(cause))} sentences; one")
    return issues


CARDINALS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def check_numbers(rec: dict, answer: str) -> list[str]:
    """Every count an answer states must be a count the annotation supports.

    Written from the one factual error in job 29510, which every other check passed.
    The facts listed Multiple Attempts on occurrences 1, 2, 4, 6 and 7 -- five of them
    -- and the answer said "six of the passes required multiple tries". Fluent,
    correctly localized, correctly negative about the rest, and wrong. A reader without
    the annotation open beside them cannot catch that, which is exactly the kind of
    error a checker is for.

    Only cardinals from two upward. "One" is nearly always article-like in this
    register -- "in one motion", "a single pass" -- and checking it produced nothing
    but noise.
    """
    g = rec.get("grounding") or {}
    total = g.get("occurrences")
    if not total:
        return []
    faults = g.get("faults") or []
    # What a truthful answer can legitimately count: how many occurrences there were,
    # how many went wrong, how many did not, and how many carry each error type. That
    # last one is what the failing answer got wrong -- it is a subset of the faults,
    # not the fault count.
    per_type: Counter = Counter()
    for f in faults:
        for t in f.get("error_types", []):
            per_type[t] += 1
    allowed = {total, len(faults), total - len(faults)} | set(per_type.values())
    allowed.discard(0)

    # Only numbers in a partitive construction -- "six of the passes", "two of them".
    #
    # The first version took every cardinal in the answer and produced two false
    # positives immediately: "Occurrences two and three presented the needle at an
    # incorrect orientation" states occurrence INDICES, not counts, and 3 is not a
    # count the annotation supports for a record with 2 faults. Allowing every index
    # 1..total instead would have made the check useless -- the error it exists to
    # catch, "six of the passes" on a 7-occurrence record, is itself within 1..7.
    #
    # The partitive is what distinguishes the two. "Six OF THE passes" is a quantity;
    # "occurrences two and three" is a pair of names. Narrow, but it catches the real
    # failure mode and cannot fire on an identifier.
    num = r"\d+|" + "|".join(CARDINALS)
    stated: set[int] = set()
    for m in re.finditer(rf"\b({num})\s+of\s+(?:the|them|those|these)\b",
                         answer, re.IGNORECASE):
        w = m.group(1).lower()
        stated.add(int(w) if w.isdigit() else CARDINALS[w])
    wrong = sorted(n for n in stated if n not in allowed)
    if wrong:
        detail = ", ".join(f"{t} {n}" for t, n in sorted(per_type.items()))
        return [f"states a count the annotation does not support: {wrong} "
                f"(occurrences {total}, faults {len(faults)}, clean "
                f"{total - len(faults)}" + (f", {detail}" if detail else "") + ")"]
    return []


# A fault answer volunteering that the other occurrences were fine. The guidance says
# "Do not comment on the occurrences that were fine", and it has been ignored in three
# separate batches -- "while the first three passes completed without issue",
# "proceeded without incident during the first three attempts". Not a correctness
# problem: the statement is true and annotation-supported. It is a scope problem, and
# it went unnoticed for three batches precisely because nothing looked for it.
#
# Only fires where some occurrences were clean and some were not. When every
# occurrence is a fault there is nothing to volunteer, and a wholly clean record is
# supposed to say exactly this.
# Which occurrences the answer names, so they can be compared against which ones are
# faults. This replaces a lexical CLEAN_ASIDE that matched "an ordinal near a
# went-well verdict", and which on one batch simultaneously missed "while the first
# three finished cleanly" -- because "finished cleanly" was not in its verb list --
# and fired on "proceeded with a misoriented grip during the first and second
# attempts", which is the fault itself being reported. Wrong in both directions at
# once is the signal that a judgement is not regex-shaped, and it was the third batch
# running in which this one had been wrong.
#
# Structural instead. The question is not "does this sentence sound positive" but "does
# it talk about an occurrence that was not a fault", and the grounding knows exactly
# which those are. "The first three" expands to 1, 2, 3; "the other" and "the
# remaining" mean the non-faults by definition; a bare ordinal is itself.
ORDINAL_TO_INDEX = {w: i for i, w in enumerate(
    ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
     "ninth", "tenth"), start=1)}
COUNT_WORD = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10}
OTHERS = re.compile(r"\b(?:the )?(?:other|others|remaining|rest)\b", re.IGNORECASE)
FIRST_N = re.compile(r"\b(?:the )?first\s+(two|three|four|five|six|seven|eight)\b",
                     re.IGNORECASE)


def occurrences_named(answer: str) -> set[int]:
    """Which occurrence indices an answer refers to."""
    named: set[int] = set()
    for m in FIRST_N.finditer(answer):
        named |= set(range(1, COUNT_WORD[m.group(1).lower()] + 1))
    text = FIRST_N.sub(" ", answer)
    for w in re.findall(r"\b(" + "|".join(ORDINAL_TO_INDEX) + r")\b", text, re.I):
        named.add(ORDINAL_TO_INDEX[w.lower()])
    for m in re.finditer(r"\b(?:occurrences?|attempts?|passes|transfers?|instances?|"
                         r"times)\s+(\d+)\b", answer, re.IGNORECASE):
        named.add(int(m.group(1)))
    return named


# The facts' own numbering format appearing in trainee-facing text. "Occurrence 2 of
# 4 and occurrence 3 of 4 were executed with the needle presented at the wrong
# orientation" is correct and scoreable and reads like a database row. The facts now
# spell the ordinal out for exactly this reason; this catches a relapse.
RECORD_FORMAT = re.compile(r"\boccurrences?\s+\d+\s+of\s+\d+\b", re.IGNORECASE)


def check_scope(rec: dict, answer: str) -> list[str]:
    """A fault answer should report the faults, not review the clean occurrences."""
    issues: list[str] = []
    m = RECORD_FORMAT.search(answer)
    if m:
        issues.append(f"answer uses the facts' numbering format ({m.group(0)!r}); "
                      f"say 'the second' as the guidance asks")
    g = rec.get("grounding") or {}
    faults = g.get("faults") or []
    total = g.get("occurrences") or 0
    if not faults or len(faults) >= total:
        return issues
    fault_idx = {f["index"] for f in faults}
    if OTHERS.search(answer):
        issues.append("answer refers to 'the other' occurrences, which are by "
                      "definition the ones that were fine; the guidance asks for the "
                      "faults only")
        return issues
    extra = sorted(occurrences_named(answer) - fault_idx)
    if extra:
        issues.append(f"answer refers to occurrence(s) {extra} that were not faults "
                      f"(faults: {sorted(fault_idx)} of {total}); the guidance asks "
                      f"for the faults only")
    return issues


def check_locate_answer(rec: dict, answer: str) -> list[str]:
    """A locate answer has to say WHICH occurrence, because the question would not.

    This is the whole point of the form: "two of those three transfers" is true and
    unscoreable, since it does not say which two. Naming them is what makes the record
    checkable against the stored occurrence indices.
    """
    g = rec.get("grounding") or {}
    faults = g.get("faults") or []
    total = g.get("occurrences") or 0
    if not faults:
        # Both checks always run. The first version returned early when NO_FAULT
        # missed, so an answer whose negative was phrased in words the pattern did
        # not know ("executed cleanly") was reported as failing to state a negative
        # and never checked for the praise it actually contained -- one wrong issue
        # in place of the two right ones.
        issues = [] if NO_FAULT.search(answer) else [
            "no occurrence was an error, and the answer does not plainly say so "
            "(or says it in wording NO_FAULT does not yet cover -- check by eye)"]
        return issues + check_clean_answer(answer)
    named = {w.lower() for w in ORDINAL_WORD.findall(answer)}
    # Cardinals count as naming an occurrence when they follow an occurrence noun.
    # "Occurrences two and three presented the needle at an incorrect orientation"
    # identifies them exactly as well as "the second and third" does, and the
    # ordinals-only version rejected it as an unscoreable count -- the opposite of
    # what this check is for.
    named |= {m.group(0).lower() for m in CARDINAL_ID.finditer(answer)}
    if ALL_WORD.search(answer) and len(faults) == total:
        return []
    if not named:
        return [f"answer does not name which occurrence went wrong "
                f"(truth: {[f['index'] for f in faults]} of {total}); a count alone "
                f"cannot be scored against the annotation"]
    return []


def check_no_extra_finding(rec: dict, answer: str) -> list[str]:
    """The answer must not assert an error that was never recorded.

    check_answer_states_finding asks whether the recorded error is reported. Nothing
    asked the opposite question until an answer to a Multiple Attempts record read
    "the trainee misaligned the needle on the initial insertion and required multiple
    passes". The second half is the recorded error; the first half is an orientation
    error that no annotation contains. It passed every check, because reporting the
    right finding and inventing an additional one are independent failures.

    The overreach arrived with the instruction to phrase things in the model's own
    words rather than the annotation's -- given room to rephrase, it elaborated. So
    this is the cost of readable answers, and it needs a check rather than a
    retreat.

    Answer only. inferred_cause is allowed to hypothesise; that is its whole purpose.
    """
    if rec.get("question_kind") != "locate":
        return []
    g = rec.get("grounding") or {}
    recorded = set(g.get("error_types") or [])
    issues = []
    for etype, pattern in ASSERTS_FINDING.items():
        if etype in recorded:
            continue
        m = re.search(pattern, answer, re.IGNORECASE)
        if m:
            issues.append(f"answer asserts a {etype!r} finding ({m.group(0)!r}) that "
                          f"was never recorded; recorded here: "
                          f"{sorted(recorded) or 'nothing'}")
    return issues


# Which object each recorded error is about. An answer may not leave it out or swap
# it for another.
#
# Two of six fault answers in one batch did: "The second and third passes were
# presented at an incorrect angle for positioning" never says what was at the wrong
# angle, and "the trainee presented the instrument at an incorrect angle" names the
# instrument, where the annotation records the needle. Both read as fluency slips and
# are not -- Needle Orientation is a fact about the needle, so an answer that reports
# it of something else has reported a different fact.
#
# Not applied to Multiple Attempts: "the step needed several attempts" is about the
# step, and has no object to name.
FINDING_SUBJECT = {
    "Needle Orientation": (r"\bneedle\b", "needle"),
    "Needle Drop": (r"\bneedle\b", "needle"),
}


def check_finding_subject(rec: dict, answer: str) -> list[str]:
    """The answer must name the thing the recorded error is about."""
    issues = []
    for etype in set((rec.get("grounding") or {}).get("error_types") or []):
        spec = FINDING_SUBJECT.get(etype)
        if spec and not re.search(spec[0], answer, re.IGNORECASE):
            issues.append(f"{etype!r} is a fact about the {spec[1]}, which the answer "
                          f"never names")
    return issues


def check_answer_states_finding(rec: dict, answer: str) -> list[str]:
    """The answer must report what the annotations showed; the question no longer does."""
    kind = rec.get("question_kind")
    if kind == "locate":
        issues = (check_locate_answer(rec, answer) + check_numbers(rec, answer)
                  + check_scope(rec, answer))
        for etype in {t for f in (rec.get("grounding") or {}).get("faults", [])
                      for t in f.get("error_types", [])}:
            pattern = FINDING_TERMS.get(etype)
            if pattern and not re.search(pattern, answer, re.IGNORECASE):
                issues.append(f"answer never reports the recorded {etype!r}")
        return issues
    if kind == "error":
        for etype in (rec.get("grounding") or {}).get("error_types", []):
            pattern = FINDING_TERMS.get(etype)
            if pattern and not re.search(pattern, answer, re.IGNORECASE):
                return [f"answer never reports the recorded {etype!r}, which the question "
                        f"withheld -- the trainee learns nothing from it"]
        return []
    if kind in ("outlier", "wandering") and not re.search(MEASURE_TERMS, answer, re.I):
        return ["answer never reports the measurement the question withheld"]
    return []

# Mechanics nothing in an event question can measure. The kinematics do record
# tooltip position and gripper angle, but no event question reports them, so an
# answer naming them is going beyond its own evidence.
UNMEASURED = re.compile(
    r"\b(wrist\w*|elbow\w*|forearm\w*|shoulder\w*|posture|"
    r"grip (?:pressure|force|strength|tension)|"
    r"finger (?:tension|placement|position)|"
    r"tip control|tremor\w*|hand tremor|"
    r"depth perception|bimanual)\b",
    re.IGNORECASE,
)
META = re.compile(
    r"\b(scores?|scored|scoring|subscore\w*|rubrics?|ratings?|percentile\w*|"
    # median, standard and top performers all slipped past the first version and
    # appeared in the 8-4 batch: they name the comparison as a construct rather
    # than stating what happened, which is what rule 5 bars.
    r"benchmark\w*|metrics?|grading|assessments?|medians?|standards?|"
    r"top performers?|typical levels?)\b",
    re.IGNORECASE,
)
PATIENT = re.compile(
    r"\b(patient\w*|bleed\w*|h(a)?emorrhag\w*|ischemi\w*|ischaemi\w*|necrosis|"
    r"perfusion|healing|wound|incision|fascia|postoperative|complication\w*|"
    r"iatrogen\w*)\b",
    re.IGNORECASE,
)
# words that mark criticism vs approval, for the contradiction check
NEGATIVE = re.compile(r"\b(delay\w*|slow\w*|excess\w*|unnecessary|inefficien\w*|"
                      r"fragment\w*|below|struggl\w*|hesitat\w*|wasted|too (?:long|many|much))\b",
                      re.IGNORECASE)
# Words that mark approval of what happened. Deliberately excludes "direct" and
# "commit", even though both read as positive: the events system prompt prescribes
# exactly those words for corrections ("commit to a single direct path instead of
# adjusting course mid-reach"), so an answer following instructions on a fault
# question used them and was flagged as praising the fault. Two of nineteen
# records, both plainly critical. A checker must not penalise the vocabulary the
# prompt it is checking hands out.
POSITIVE = re.compile(r"\b(efficient\w*|well[- ]established|outperform\w*|faster|"
                      r"solid|reliable|excellent|commendable)\b",
                      re.IGNORECASE)


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def check_record(rec: dict) -> list[str]:
    issues: list[str] = []
    if rec.get("validation_status") != "valid" or not rec.get("qa"):
        return [f"REJECTED: {rec.get('validation_error')}"]
    qa = rec["qa"][0] if isinstance(rec["qa"], list) else rec["qa"]
    question = str(qa.get("question", ""))
    answer = str(qa.get("answer", ""))
    if not answer.strip():
        return ["empty answer"]

    # The trainee is described to a supervisor, not addressed. Reported with the
    # match so a stray "your" in an otherwise good answer is quick to find; every
    # answer in the batch that prompted this check used second person, so the first
    # run after the prompt change is expected to be all-or-nothing either way.
    m = SECOND_PERSON.search(answer)
    if m:
        issues.append(f"answer is second person ({m.group(0)!r}); the batch is third "
                      f"person throughout")

    # unmeasured mechanics: flagged only when the question did not raise them.
    #
    # Checked in inferred_cause as well as in the answer. Rule 4 is absolute -- the
    # wrists, elbows, grip pressure and tremor are not visible in any of the three
    # sources, so a hedge does not license them: "consistent with insufficient wrist
    # rotation during the reach" is still a claim about a wrist nobody saw. Scoping
    # this to the answer let one straight through on the first split batch, because
    # the cause field was new and nothing had been pointed at it.
    for field, text in (("answer", answer),
                        ("inferred_cause", str(qa.get("inferred_cause", "") or ""))):
        for m in UNMEASURED.finditer(text):
            term = m.group(0)
            if not re.search(re.escape(term), question, re.IGNORECASE):
                issues.append(f"{field} asserts a mechanic nothing measured: {term!r}")
                break

    # Same exemption as above, and for the same reason: the economy question says
    # "the median across other recorded attempts" and "the typical level for this
    # task", so an answer echoing those is quoting its own question, not appealing
    # to a rubric. Flagging it would repeat the mistake of penalising an answer for
    # using the words it was handed.
    # Narrating how the finding was obtained rather than what happened. Distinct from
    # the META check: "a reviewer recorded a needle drop" cites no rubric, but it tells
    # the trainee about the annotation process instead of about their hands.
    #
    # Scoped differently per field, because the two fields have different jobs. The
    # answer is trainee-facing and must never do it. The rationale exists to say which
    # fact the answer rests on, so "rests on the recorded annotation that the needle was
    # presented at a wrong orientation" is exactly right there -- barring that was the
    # same over-correction as loading the questions with measurements. What stays barred
    # everywhere is the human reviewer as a character, which is what prompted this
    # check: 11 of 12 rationales came back narrating "the reviewer's explicit note".
    PERSONA = r"\b(a )?reviewers?\b|\bannotators?\b|\bwatching the video\b"
    # "recorded" in any construction except the comparison the questions themselves
    # use. The first version matched only "recorded that/a/an", and an answer wrote
    # "no errors were recorded for either attempt" -- the fourth time this vocabulary
    # has leaked into trainee-facing text, and the third distinct phrasing. The
    # exception is load-bearing: outlier and wandering questions say "the strongest
    # recorded attempts", so an answer echoing that is quoting its own question.
    # The whole passive family, not just "recorded". Each leak so far has used a
    # different verb, so barring them one at a time only ever catches last time's.
    PROCESS = PERSONA + (r"|\brecorded\b(?!\s+attempts?\b)|\bannotation\w*\b|"
                         r"\bthe (labels?|dataset)\b|"
                         r"\b(?:was|were|are|is)\s+(?:not\s+)?"
                         r"(?:noted|logged|flagged|identified|documented|marked|"
                         r"observed|reported)\b|"
                         r"\bno\s+\w+\s+(?:was|were)\s+\w+ed\b")
    for field, text, pattern in (("answer", answer, PROCESS),
                                 ("rationale", str(qa.get("rationale", "")), PERSONA)):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            issues.append(f"{field} narrates how the finding was obtained "
                          f"({m.group(0)!r}) instead of stating what happened")

    for m in META.finditer(answer):
        term = m.group(0)
        if not re.search(re.escape(term), question, re.IGNORECASE):
            issues.append(
                f"refers to the measurement apparatus rather than the fact: {term!r}")
            break
    m = PATIENT.search(answer)
    if m:
        issues.append(f"real-patient consequence on a bench-top recording: {m.group(0)!r}")

    # anchoring: the answer must point at the span or the action the question named
    span = rec.get("span", "")
    span_parts = [p for p in re.split(r"[-]", span) if p]
    # The action is read out of the question rather than matched against a list.
    #
    # The list was needle|suture|instrument|reach|orient|position|push|pull|transfer,
    # which covers most gestures and silently fails on the ones it does not. G5 is
    # "moving to the centre with the needle in grip", and the answer "all of them went
    # as intended when moving to the centre" names that action exactly -- but neither
    # "moving" nor "centre" was in the list, so it was reported as naming no action at
    # all. Same enumerate-what-you-have-seen mistake as ALL_WORD, NO_FAULT and
    # FINDING_TERMS before it.
    #
    # Every locate question contains the gesture label after "the trainee was", so the
    # question can say what its own action words are.
    # The capture has to be stripped of the question's own scaffolding, or the check
    # goes lax rather than strict. "This recording shows the trainee was positioning
    # the needle on several occasions" yields "positioning the needle on several
    # occasions", and an answer reading "The trainee required several attempts before
    # the fourth attempt succeeded" -- which names no action at all -- then counts as
    # anchored because it shares the word "several". A false negative is worse here
    # than the false positive that prompted the change.
    m_label = re.search(r"trainee was (.+?)(?:\s+in this recording|[.?,;]|$)",
                        question, re.IGNORECASE)
    if m_label:
        action_words = {w for w in re.findall(r"[A-Za-z']+", m_label.group(1))
                        if w.lower() not in OVERLAP_STOP
                        and w.lower() not in QUESTION_SCAFFOLD and len(w) > 2}
    else:
        action_words = set(re.findall(
            r"\b(?:needle|suture|loop|instrument|reach\w*|orient\w*|"
            r"position\w*|push\w*|pull\w*|transfer\w*|segment\w*)\b",
            question, re.IGNORECASE))
    # Matched on a stem, not the whole word. The question says "pulling suture" and the
    # answer says "during that left-hand pull", which is the same action named naturally
    # -- an exact-word match called three such answers unanchored.
    def stem(w: str) -> str:
        return re.sub(r"(ing|ed|s)$", "", w.lower())[:5]

    answer_stems = {stem(w) for w in re.findall(r"[A-Za-z']+", answer)}
    anchored = any(p and p in answer for p in span_parts) or any(
        stem(w) in answer_stems for w in action_words)
    if not anchored:
        issues.append("never refers to the span or the action the question named")

    # Only for records generated under the split schema. Older batches kept the cause
    # inside the answer by design, and flagging every one of them would bury the
    # checks that still apply to them.
    if rec.get("answer_schema") == "split":
        issues += check_answer_inference_free(rec, qa)

    issues += check_question(rec, question, answer)
    issues += check_answer_states_finding(rec, answer)
    issues += check_no_extra_finding(rec, answer)
    issues += check_finding_subject(rec, answer)
    # Clean answers only. A fault answer names other actions for good reason: asked
    # about positioning, it explains that the needle was not seated before the pull
    # began, and asked about pushing, it reports the recorded orientation error. Run
    # unscoped this check produced 7 flags of which 3 were real, and the 4 false ones
    # were all answers doing exactly what the guidance asks. A clean answer has no
    # such licence -- its whole content is "this action went fine", so a different
    # action in it is simply the wrong action.
    if rec.get("question_kind") == "locate" and not (rec.get("grounding") or {}).get("faults"):
        issues += check_action_matches(question, answer)

    sents = sentences(answer)
    words = len(answer.split())
    if len(sents) > MAX_SENTENCES or words > MAX_WORDS:
        issues.append(f"too long: {len(sents)} sentences, {words} words "
                      f"(limit {MAX_SENTENCES}/{MAX_WORDS})")

    # The polarity check that used to live here is gone. It asked whether an answer
    # praised a span the question reported as a fault, and across this session it
    # produced six false positives and no true ones. Twice it caught the prompt's own
    # prescribed vocabulary ("commit to a direct path"); four more times it caught
    # answers describing the comparison group -- "three times farther than the most
    # efficient attempts" is criticism, and "efficient" there refers to the other
    # trials, not the trainee. Restricting it to the first sentence made it worse,
    # because the criticism often lands in the second.
    #
    # The underlying judgement -- does this answer take the right stance toward what
    # was measured -- is not a regex judgement. It belongs to the LLM-as-judge pass,
    # which reads the answer against the annotation rather than matching word lists.
    # A check that is wrong every time it fires is worse than no check: it makes a
    # clean batch look dirty and teaches you to skim past the output.
    return issues


# Fraction of a batch that may share one phrase before it counts as boilerplate.
# Set from a measured failure: in the 8-4 batch 74% of answers said "mid-reach" or
# "mid-action" and 51% said "commit to a direct path", so every answer named its
# own span and then gave the same advice about it. Per-record checks cannot see
# that -- each answer is individually fine -- which is why this check has to look
# at the batch. It is the reviewer's original complaint reappearing one sentence
# further in, and the metric that missed it (are first sentences specific?) is
# satisfied by merely restating the question.
BOILERPLATE_SHARE = 0.4
BOILERPLATE_MIN_RECORDS = 8     # below this, a shared phrase is not yet evidence

# Phrases worth watching, from what the model actually converged on. Not an
# exhaustive list of bad writing -- a diversity floor on 5-grams would be
# stricter but would also fire on the vocabulary the prompt deliberately
# prescribes, so these are the specific collapses observed.
WATCH = {
    "mid-reach / mid-action": r"mid-(?:reach|action|motion|stroke)",
    "commit to a direct path": r"commit\w*\s+to\s+(?:a|one|the)?\s*\w*\s*(?:direct\s+)?"
                               r"(?:path|trajectory|approach)",
    "adjusting course": r"adjust\w*\s+(?:your\s+)?course|correct\w*\s+(?:your\s+)?"
                        r"(?:course|route)",
    "hesitating": r"hesitat\w*",
    "plan before moving": r"plan\w*\s+(?:the|your|each)\b",
}


# Repeated word sequences worth allowing. Rule 7 asks for an action the trainee can
# take on the next repetition, so answers say "on your next repetition"; that is the
# prompt being followed, not the answers converging on a diagnosis.
STRUCTURAL_PHRASES = ("on your next repetition", "on the next repetition",
                      "on your next attempt")
NGRAM_SHARE = 0.5      # a phrase in half the answers is a template, not a coincidence
NGRAM_SIZES = (4, 5)


def repeated_phrases(answers: list[str],
                     facts: list[str] | None = None) -> list[tuple[str, int]]:
    """Word sequences appearing in at least NGRAM_SHARE of the answers.

    Needed because WATCH is a list of phrases already known to have gone wrong, so it
    can only catch a repeat of a past failure. When the correction vocabulary was
    broadened, every WATCH phrase fell below its limit while "jaw" appeared in 74% of
    answers -- a check built from history would have called that a clean batch. This
    looks for whatever the answers actually share.
    """
    counts: Counter = Counter()
    for a in answers:
        words = re.findall(r"[a-z']+", a.lower())
        for size in NGRAM_SIZES:
            counts.update({" ".join(words[i:i + size])
                           for i in range(len(words) - size + 1)})
    # A phrase the annotations themselves use is fidelity, not filler. "At the wrong
    # orientation" appeared in 4 of 8 answers and in the facts given to all four of
    # those records -- it is the label's own wording, and under the split schema the
    # answer's entire job is to state the label. Flagging it would push the model to
    # paraphrase an annotation it should be quoting, which is how a verifiable
    # statement turns into an approximate one.
    # Phrases lifted from the annotation are reported, not dropped.
    #
    # They were exempted outright when the answer's job was to state the label, on
    # the reasoning that quoting it was fidelity. Review disagreed with the output
    # that produced -- "presented at the wrong orientation for the step" appeared
    # verbatim, eight words at a stretch, in half a batch, and reads like a database
    # row. The claim must be supported by the annotation; the wording should not be
    # copied from it. So these still surface, marked, and the caller prints them as
    # notes rather than failures -- a shared phrase here is a prompt problem to weigh,
    # not a defect in any single record.
    from_annotation = set()
    if facts:
        blob = " ".join(re.findall(r"[a-z']+", " ".join(facts).lower()))
        from_annotation = {p for p in counts if p in blob}
    hits = [(phrase, n) for phrase, n in counts.items()
            if n / len(answers) >= NGRAM_SHARE
            and not any(s in phrase for s in STRUCTURAL_PHRASES)]
    # Longest first, so "commit to a single direct" is reported rather than the
    # several shorter fragments of itself that also cross the threshold.
    hits.sort(key=lambda kv: (-len(kv[0].split()), -kv[1]))
    out: list[tuple[str, int, bool]] = []
    for phrase, n in hits:
        if not any(phrase in kept for kept, _, _ in out):
            out.append((phrase, n, phrase in from_annotation))
    return out[:5]


# How much of a batch may open with the same two words. This check exists because of
# the fix that created the risk: telling the model to write in the third person makes
# "The trainee ..." the path of least resistance for every first sentence, so a batch
# can satisfy the second-person rule and read more templated than the batch it
# replaced. Three phrases from earlier versions of the events prompt came back
# verbatim across a whole batch, so this is an observed failure mode rather than a
# hypothetical one.
#
# Two words rather than one: "the trainee" and "the needle" are different openings,
# and "the" alone would report nothing.
OPENER_SHARE = 0.4
OPENER_MIN_RECORDS = 5


# The hedges the system prompt offers, checked as a set rather than as phrases.
#
# This needs its own check because the generic n-gram detector cannot see it. "Follows
# from" reached 62% of a batch and was not reported: the shared span is three words,
# NGRAM_SIZES starts at four, and the four-word windows around it differ ("that
# pattern typically follows" against "that sequence typically follows"). A collapse
# onto one hedge is the specific failure this prompt has now had twice, so it gets a
# specific check rather than relying on a general one to notice.
HEDGE_PATTERNS = {
    "the usual cause of this is": r"\bthe usual cause\b",
    "this is what happens when": r"\bwhat happens when\b",
    "that pattern points to": r"\bpoints? to\b",
    "consistent with": r"\bconsistent with\b",
    "typically this follows from": r"\bfollows from\b",
    "most likely": r"\bmost likely\b",
}
HEDGE_SHARE = 0.4


def hedge_collapse(answers: list[str]) -> list[tuple[str, int]]:
    """Hedges used by more than HEDGE_SHARE of a batch."""
    out = []
    for label, pattern in HEDGE_PATTERNS.items():
        n = sum(1 for a in answers if re.search(pattern, a, re.IGNORECASE))
        if n and n / len(answers) >= HEDGE_SHARE and n > 1:
            out.append((label, n))
    return sorted(out, key=lambda kv: -kv[1])


def repeated_openers(answers: list[str]) -> list[tuple[str, int]]:
    """Opening bigrams shared by at least OPENER_SHARE of the answers."""
    counts: Counter = Counter()
    for a in answers:
        words = re.findall(r"[a-z']+", a.lower())[:2]
        if len(words) == 2:
            counts[" ".join(words)] += 1
    return [(p, n) for p, n in counts.most_common()
            if n / len(answers) >= OPENER_SHARE and n > 1]


def group_of(rec: dict) -> str:
    """What an answer is about, for grouping the repetition checks.

    Aggregating over a whole batch is misleading when the batch is not uniform.
    "mid-reach" hit 41% of one 27-record batch and was flagged -- but it was 11 of 12
    Multiple Attempts records and 0 of the 15 Needle Drop and Needle Orientation ones.
    For a step that took several attempts, "adjusted course mid-reach" IS the
    diagnosis; the 41% was measuring the batch's error-type mix, not its writing. A
    reviewer given one aggregate number cannot tell those apart, and acting on it
    would have damaged the answers that were right.
    """
    types = (rec.get("grounding") or {}).get("error_types")
    if types:
        return types[0]
    return rec.get("question_kind", "?")


def check_batch(recs: list[dict]) -> list[str]:
    """Issues visible only across a batch, not in any single record."""
    valid = [r for r in recs if r.get("validation_status") == "valid" and r.get("qa")]
    answers = [str((r["qa"][0] if isinstance(r["qa"], list) else r["qa"]).get("answer", ""))
               for r in valid]
    issues: list[str] = []

    # Checked at a lower record count than the phrase checks below, because an
    # opening is one bigram per answer rather than a distribution over many, so it
    # saturates visibly on a batch far too small to say anything about boilerplate.
    if len(answers) >= OPENER_MIN_RECORDS:
        for phrase, n in repeated_openers(answers):
            issues.append(f"repeated opening: {n}/{len(answers)} answers "
                          f"({n / len(answers):.0%}) begin {phrase!r}")
        for label, n in hedge_collapse(answers):
            issues.append(f"hedge collapse: {n}/{len(answers)} answers "
                          f"({n / len(answers):.0%}) use {label!r}; the prompt assigns "
                          f"one of {len(HEDGE_PATTERNS)} per record for this reason")

    if len(answers) < BOILERPLATE_MIN_RECORDS:
        return issues

    # Grouped: a phrase is boilerplate when it saturates the answers it belongs to.
    groups: dict[str, list[str]] = {}
    for rec, ans in zip(valid, answers):
        groups.setdefault(group_of(rec), []).append(ans)

    for label, pattern in WATCH.items():
        n = sum(1 for a in answers if re.search(pattern, a, re.IGNORECASE))
        if not n:
            continue
        share = n / len(answers)
        # Which groups it actually came from, so a flag can be acted on.
        per = {g: sum(1 for a in gans if re.search(pattern, a, re.IGNORECASE))
               for g, gans in groups.items()}
        hit_groups = [g for g, c in per.items() if c]
        spread = len(hit_groups) > 1 or len(groups) == 1
        if share >= BOILERPLATE_SHARE and spread:
            detail = ", ".join(f"{g} {per[g]}/{len(groups[g])}" for g in sorted(hit_groups))
            issues.append(f"boilerplate: {n}/{len(answers)} answers ({share:.0%}) "
                          f"use {label!r} across {len(hit_groups)} kinds -- {detail}")
        elif share >= BOILERPLATE_SHARE:
            g = hit_groups[0]
            issues.append(f"note: {label!r} is {per[g]}/{len(groups[g])} of {g!r} answers "
                          f"and absent elsewhere, so it is on-topic rather than filler "
                          f"({share:.0%} of the batch reflects its error-type mix)")

    # Word sequences shared across the whole batch regardless of group: those cannot
    # be explained by subject matter, so they are always worth reporting.
    for phrase, n, from_facts in repeated_phrases(
            answers, [str(r.get("facts_shown_to_model", "")) for r in valid]):
        if from_facts:
            issues.append(f"note: {n}/{len(answers)} answers ({n / len(answers):.0%}) "
                          f"reuse the annotation's own wording {phrase!r} -- the claim "
                          f"should come from the annotation, the phrasing should not")
        else:
            issues.append(f"repeated phrasing: {n}/{len(answers)} answers "
                          f"({n / len(answers):.0%}) contain {phrase!r}")
    return issues


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 1
    grand_total = grand_issues = unusable = 0
    for p in paths:
        path = Path(p)
        f = path / "qa_records.jsonl" if path.is_dir() else path
        if not f.exists():
            print(f"!! missing: {f}")
            unusable += 1
            continue
        recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not recs:
            print(f"\n=== {f} (0 records) ===")
            print("  !! empty -- nothing was checked")
            unusable += 1
            continue
        print(f"\n=== {f} ({len(recs)} records) ===")
        n = 0
        for r in recs:
            issues = check_record(r)
            if issues:
                n += len(issues)
                print(f"  {r.get('question_kind', '?'):11s} {r.get('trial_id', '?'):22s} -> {issues}")
        # Lines beginning "note:" are context, not defects -- a phrase that saturates
        # one error type and appears nowhere else is on-topic. Counting them would make
        # a clean batch read as failing, which is the fastest way to teach someone to
        # ignore this output.
        batch = check_batch(recs)
        for issue in batch:
            label = "NOTE" if issue.startswith("note:") else "BATCH"
            print(f"  {label:11s} {'-':22s} -> {issue}")
        n += sum(1 for issue in batch if not issue.startswith("note:"))
        if not n:
            print("  all clean")
        grand_total += len(recs)
        grand_issues += n
    print(f"\n{'=' * 70}")
    print(f"TOTAL: {grand_total} records, {grand_issues} issues")
    if unusable:
        print(f"{unusable} input(s) missing or empty -- NOT checked")
    return 1 if grand_issues or unusable else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
