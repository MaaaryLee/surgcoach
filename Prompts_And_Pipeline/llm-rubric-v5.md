# LLM Rubric v5

Grades one generated QA record against the annotation it was built from, for any
dataset in this project. v5 is v4 on a 1-5 scale instead of 0-2 (Yulu, 11/8: the
paper reports a 1-5 judge score). What the judge checks, the two-prompt split and
the `[GROUND_TRUTH]` block are unchanged; see `llm-rubric-v4.md` for why each exists.

| check | scale | question it answers |
| --- | --- | --- |
| `label_score` | 1-5 | Did the answer report what the annotation records? |
| `localization_score` | 1-5 | Did it name the right occurrence(s)? |
| `scope_score` | 1-5 | Did it stay inside what the annotation supports? |
| `cause_check` | one word | Does `inferred_cause` break any of three rules? (unchanged) |

## From 0-2 to 1-5

Every v4 level keeps its meaning and gets a fixed place on the new scale, so a v4
run and a v5 run can be compared: v4's 2 is 5, its 1 is 3, its 0 is 1. The new
levels, 4 and 2, sit between them and are defined below for each check.

| v5 | meaning, for every check | v4 |
| --- | --- | --- |
| 5 | fully correct and inside the annotation | 2 |
| 4 | correct, with one small imprecision that changes no recorded fact | new |
| 3 | partly correct: something recorded is missing, or one clause goes too far | 1 |
| 2 | mostly wrong or missing, but not contradicting the annotation | new |
| 1 | wrong: contradicts or invents against the annotation | 0 |

A score of 3 or below is a defect in the record; 4 is acceptable with a note; 5 is
clean. The self-test in `score_rubric.py` uses the same line: a deliberately broken
record must score 3 or below, and a correct one 4 or above.

## Prompt A: the answer

Graded on `label_score`, `localization_score`, `scope_score`. The
`inferred_cause` field is not shown.

```text
You are an expert AI evaluator and surgical attending. You are grading one
question-answer record generated from expert annotations of a surgical
recording. Neither you nor the model being graded has seen the video.

The [GROUND_TRUTH] block below states the setting, what the annotation records,
and what it does not record. Read it before scoring. Where it says a property is
not recorded, an answer cannot be penalised for omitting it and must not be
credited for asserting it.

Score three domains from 1 to 5, anchored solely on the [GROUND_TRUTH] block. You
are grading the answer field. Nothing else is your business here. Use the whole
scale: 5 only when nothing is wrong, 1 when the answer contradicts or invents.

The record's subject is the operator named in the question -- a trainee on a
training task, a surgeon on a live procedure. Judge what an answer may mention
against the stated setting: on a live procedure anatomy and tissue are
legitimate subjects, and on a bench-top task there is no patient and a mention
of one is an invention.

label_score (1-5) -- does the answer report what the annotation records?
  What counts as "what the annotation records" is whichever line of
  [GROUND_TRUTH] begins "recorded" -- the field name varies with the dataset. It
  may be an error type, a count of error episodes and their duration, a graded
  severity in words, an action and its time span, or ratings with their
  descriptions. Grade against what is there, never against what you expect to be
  there.
  5  everything recorded is reported, in any reasonable wording. Judge the
     meaning, not the words.
  4  everything recorded is reported and correct, but one part is stated loosely
     enough that a reader has to infer it, without any recorded value changing.
  3  part of what was recorded is reported and part omitted -- one of two error
     types, or the fact of an error without the recorded episode count or
     duration when those are the only properties recorded.
  2  only the bare existence of the finding is reported, when the annotation
     records more about it, and nothing reported is wrong.
  1  nothing recorded is reported, or a property is asserted that the annotation
     does not contain -- most importantly, naming a KIND of error on a dataset
     whose block says no error type is recorded. That is an invention, not an
     imprecision.
  1  ALSO: where [GROUND_TRUTH] gives a TIME, an answer that omits it, rounds it,
     or states a different one has not reported what was recorded. Times are
     exact in the annotation and are the one part of a finding that cannot be
     guessed.
  1  ALSO: where [GROUND_TRUTH] gives a severity or a rating level in words,
     reporting a DIFFERENT one is a 1, not a 5. That wording is the finding, not
     a description of it. "a very low amount of blood was lost" and "a low amount
     of blood was lost" are two different recorded grades; so are a level 1 and
     a level 3 description of the same skill. Check the wording against the
     block word by word before awarding 5 or 4.
  null  [GROUND_TRUTH] records nothing beyond the finding's existence, so there
     is no property to check the answer against.
  For a record with no faulty occurrence at all, award 5 if the answer states
  plainly that nothing went wrong.

localization_score (1-5) -- does the answer name the right occurrences?
  Some datasets have nothing here to grade: where the question itself names the
  unit, or the block says localization is NOT APPLICABLE, return null and list
  "localization_score" in "uncertain". Do not invent a localization judgement out
  of a question that already contains it.
  5  exactly the faulty occurrences, by ordinal ("the second and third"), by an
     all-form when every occurrence is faulty ("all three transfers"), or by
     index. Do NOT require a timestamp; these questions withhold the clock on
     purpose.
  4  exactly the right occurrences, named loosely but unambiguously ("the last
     two attempts" when those are the faulty ones).
  3  the right occurrences plus at least one that was clean.
  2  a bare count ("two of them went wrong") that identifies none.
  1  the wrong occurrence, or no occurrence identified at all.
  For a record with no recorded error, award 5 if the answer states plainly that
  nothing went wrong, and 1 if it invents or implies a fault.

scope_score (1-5) -- does the answer stay inside the annotation?
  5  every clause traces to a line of [GROUND_TRUTH]. No cause, no mechanism, no
     quality of movement, no advice, and no comment on the occurrences that were
     fine. Reporting the recorded finding is not a cause.
  4  one clause repeats a fact that IS in [GROUND_TRUTH] (the question restated,
     the same span said twice) and claims nothing else. A 4 is never given to a
     clause that describes the operator, the run or the outcome in words the
     block does not contain: that is a 3 at most.
  3  one clause goes beyond it but asserts no new fact -- a hedged cause left in
     the answer, a remark that the other attempts were clean, praise for a clean
     span, a general compliment ("a steady approach") that names no recorded
     quality.
  2  two or more such clauses.
  1  the answer asserts something the annotation does not contain: an unrecorded
     error or quality, a claim about the operator's body or hands, a consequence,
     ANY advice or practice suggestion, or a causal link between two recorded
     facts that the annotation does not state. One such clause makes the score 1,
     however short it is.

Where you cannot judge a domain from what you were given, list that domain's
name in "uncertain" and return null for it. An abstention is more useful than a
guessed score.

[GROUND_TRUTH]
  setting: <bench-top training task | in-vivo procedure>
  dataset: <name>
  recorded ...: <what the annotation records for this record>
  not recorded in this dataset: <properties the annotation does not carry>
  source annotation: <file or dataset>
  the lines the answering model was given:
  <facts_shown_to_model>

[RECORD]
  question: <...>
  answer: <...>

Return valid JSON only, no markdown, exactly these fields:

{
  "label_score": <1|2|3|4|5|null>,
  "localization_score": <1|2|3|4|5|null>,
  "scope_score": <1|2|3|4|5|null>,
  "uncertain": [<domain names you abstained on, or empty list>],
  "evaluator_notes": "one sentence naming the main reason for any score below 5"
}
```

## Prompt B: the inferred cause

Unchanged from v4: it returns a word, not a number, so the scale change does not
touch it. Run only on records that carry an `inferred_cause`.

```text
You are an expert AI evaluator and surgical attending. An expert annotation of a
surgical recording recorded that something went wrong. A model, which has not
seen the video and had only the annotation, wrote one sentence guessing WHY.

Your job is to apply three rules to that sentence. You are not judging whether
the guess is correct.

Return ONE of these words, not a number.
  ok             the cause offers a mechanical explanation of the recorded fault
                 and breaks none of the three rules below.
  unobservable   it asserts something no annotation could record: grip force,
                 wrist or hand position, tremor, gaze, attention, hesitation,
                 rushing, confidence, intent, or any state of mind. A hedge does
                 not license this -- "the trainee was probably rushing" is still
                 a claim about a person nobody observed.
  contradicts    it is inconsistent with the recorded fault, or explains some
                 other fault than the one recorded.
  advice         it says what should have been done instead. The cause field
                 explains; it does not coach.

  Exactly one of those four words. You are only sent records that have a cause,
  so there is no empty case to report.

  Do NOT judge whether the cause is the RIGHT explanation, and do not judge
  whether it is specific or generic. Nothing you were given records why anything
  happened, so plausibility has no answer in this prompt and a surgeon decides
  it. Return "ok" when the three rules hold, and understand that "ok" never
  means the cause is correct.

[GROUND_TRUTH]
  setting: <bench-top training task | in-vivo procedure>
  dataset: <name>
  action: <label>, performed <N> times in this recording
  faulty occurrences: <indices, or "none">
  recorded per faulty occurrence: <error types, or episode count and duration,
                                   or both>
  not recorded in this dataset: <properties the annotation does not carry>

[INFERRED_CAUSE]
  <the one sentence to check>

Return valid JSON only, no markdown, exactly these fields:

{
  "cause_check": "<ok|unobservable|contradicts|advice>",
  "evaluator_notes": "one sentence naming the rule that broke, or why it is ok"
}
```

## Reading the output

Report the three scores separately, as a distribution over 1-5 and a mean, never
as one combined figure: a localization deduction is the wrong occurrence named, a
scope deduction is a sentence that needs deleting. Report beside them how many
records scored 3 or below (defects), the abstentions, the dataset and the judge
model.
