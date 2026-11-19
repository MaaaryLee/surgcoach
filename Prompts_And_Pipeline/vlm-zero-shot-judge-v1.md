# VLM zero-shot judge v1

Scores a vision-language model's answer to one question about a surgical recording, for the
zero-shot video test (`scripts/zs_video/zs_video_run.py judge`). It is Prompt A of
[`llm-rubric-v5.md`](llm-rubric-v5.md) with five changes. Every other 1-5 anchor is v5's,
word for word:

1. **Who is graded.** The opening says the answer comes from a model that was shown frames of
   the video. v5 said neither the judge nor the model had seen it.
2. **Time tolerance.** v5 scores any rounded or shifted time as 1, because its answers were
   written from annotations that give exact times. A model that sees one frame every few
   seconds cannot be that exact. Here a time counts as the recorded one when it is within the
   frame spacing of it, and a span counts when it overlaps the recorded span. The spacing is
   stated in `[GROUND_TRUTH]`.
3. **Times are graded under localization only.** v5 gives label 1 to an answer with a missing
   or wrong time. Here `label_score` grades what happened (the kind of event, its severity, the
   rating, the verdict) and `localization_score` grades when.
4. **Unrecorded visual detail is scope, not label.** v5 gives label 1 to any property the
   annotation does not contain, because its answers came from the annotation alone. A model
   that watched the video may describe things the annotation neither records nor rules out,
   such as bleeding on Endoscapes, which records no bleeding. Those claims lower `scope_score`
   only. `label_score` drops to 1 when the answer contradicts the annotation: a different kind
   of event, the opposite verdict, or a recorded event denied. The 11-17 smoke test showed why:
   "No, it is not safe to proceed" on a frame where no CVS criterion is met scored label 1 under
   v5 wording, only because the answer also described bleeding. So the prompt tells the judge
   to set such claims aside before scoring label. It also replaces v5's "Use the whole scale:
   5 only when nothing is wrong, 1 when the answer contradicts or invents" with "a fault
   lowers only the domain it is listed under", and v5's "must not be credited for asserting
   it" with "earns no credit for asserting it. Asserting it lowers scope_score only."

   **Known limit.** On the 19 smoke answers (11-17) this wording got 15 of 18 labels right
   (one was unclear) and fixed MB140 cases the first wording got wrong (bleeding reported for a
   recorded mechanical injury had scored 5). It still gives label 1 to a correct "No, not safe" on Endoscapes
   B2 and B7 when the answer also describes bleeding. Two variants that made the judge first
   write out the recorded finding and what the answer says about it fixed those, but missed
   contradictions elsewhere ("excellent" against recorded level-1 ratings, the wrong
   strongest skill scored 5), so they were dropped. For B7 the report adds a check without
   the judge: does the answer's yes/no match the recorded CVS state.
5. **What the model was given.** The `[GROUND_TRUTH]` block ends with what the model was shown
   (the number of frames, the stretch of the recording, the spacing) instead of the annotation
   lines it was given.

The report counts `localization_score` only where `[GROUND_TRUTH]` says localization applies,
even if the judge returns a number elsewhere. Where it applies and the answer gives no time at
all, the report sets it to 1, as the anchor says. The first judge, gemma-3-27b-it, gave most
such answers 5 on the 11-17 run: 41 of 50 MB140 B5 answers from Qwen3-VL said "There is no
visible event..." with no time.

Both judges also gave localization 1 to some answers whose time falls inside the recorded span.
So where the annotation records one span (JIGSAWS VTS, MB140 B5 and B6), the report does not
use the judge's localization. It checks in code whether the answer's first time or span
overlaps the recorded span, give or take one frame spacing, which is this anchor's 5. MB140 D2
records several events, so it keeps the judge's localization. Endoscapes B5 asks when, but its
annotation records no time, so it gets no time score from either.

Spot check on the 11-17 run (Qwen3-VL answers, 12 labels over six question types, read
against the annotation by Claude, not by a surgeon): 9 agreed with the first judge, Gemma. It was too harsh
twice, when the answer named the right phase or top risk and added unrecorded detail. It was
too lenient once ("adequate control" against a recorded "Very poor" scored 3). With the
19 smoke answers, about 24 of 30 labels agree. On 7 of Qwen3.8's longer answers 4
agreed. The other 3 were too lenient: "No, not safe to clip" where all three CVS criteria
were achieved scored 5, a stapler risk where a thermal injury was recorded scored 5, and
"below the standard expected" against a recorded overall "Competent" scored 3. These 37 cases,
each with the label range a correct judge should give, are in
`results/zero-shot video - 11-17-2026/judge_check.jsonl`, and `scripts/zs_video/judge_check.py`
scores any judge on them.

**Judge check, 11-17.** The first judge, gemma-3-27b-it, and the replacement, gpt-oss-120b
(MXFP4, reasoning effort medium, temperature 1.0), were scored on these 37 cases. One expected
range was corrected after the run: the MB140 D2 answer names the later bleed, while the first
recorded event is a deep mechanical injury.

| Judge | In range, as written | Also accepting the literal reading |
|---|---|---|
| gpt-oss-120b | 25 of 37 | 32 of 37 |
| gemma-3-27b-it | 27 of 37 | 29 of 37 |

- **The literal reading.** gpt-oss takes "everything recorded" literally: the right event without
  its recorded grade, rectification or phase gets 3, not 5. That is a fair reading of the v5
  anchors. Seven cases depend on it and carry both ranges (`label_ok_alt`).
- **The errors that matter most.** On these 37 cases gpt-oss never scored a wrong answer above 3.
  Gemma gave 4 or 5 to three wrong answers (a wrong verdict, a wrong event, the wrong event to
  address first) and 1 to two correct verdicts. In the full 11-17 run gpt-oss did it once:
  Endoscapes B7_03, where all three CVS criteria were met by majority, got 5 for Qwen3.8's
  wrong "No".
- **A block artifact.** Once, gpt-oss took the block's phrasing hints, written for the 11-09
  text-only answers, as requirements. Examples are "Say it as: ..." and "Open with this verdict,
  in your own words". Stripping those hints from the judge's copy of the block is a candidate
  fix.

The 11-17 tables use gpt-oss-120b.

The judge must be a different model from every model it grades. It sees text only.

Report as v5 does: each check separately, as a distribution and a mean, with the judge model
named. `label_score` is the headline number. `scope_score` keeps v5's rule that advice and
unrecorded detail are out of scope. Some questions ask for coaching (MB140 B5, B6; the D
templates), so read their scope scores as "how much the answer adds beyond the annotation",
not as a fault.

## Prompt

```text
You are an expert AI evaluator and surgical attending. You are grading one answer
that a vision-language model gave to a question about a surgical recording. The
model was shown frames sampled from the recording, each labelled with its time in
the recording. You have not seen the video. You have the expert annotation of the
recording instead, and it is the only thing you grade against.

The [GROUND_TRUTH] block below states the setting, what the annotation records,
and what it does not record. Read it before scoring. Where it says a property is
not recorded, an answer cannot be penalised for omitting it and earns no credit
for asserting it. Asserting it lowers scope_score only.

Score three domains from 1 to 5, anchored solely on the [GROUND_TRUTH] block. You
are grading the answer field. Nothing else is your business here. Use the whole
scale. Each domain lists its own faults, and a fault lowers only the domain it is
listed under: what happened is label_score, when it happened is
localization_score, and anything the annotation does not contain is scope_score.

The record's subject is the operator named in the question -- a trainee on a
training task, a surgeon on a live procedure. Judge what an answer may mention
against the stated setting: on a live procedure anatomy and tissue are
legitimate subjects, and on a bench-top task there is no patient and a mention
of one is an invention.

Times. The model saw frames, not the full video, so it cannot be more exact than
the spacing between its frames, which [GROUND_TRUTH] states. A time in the answer
is the recorded time when it lies within that spacing of it. A span in the answer
is the recorded span when the two overlap. A time or span further off than that
is a different time.

label_score (1-5) -- does the answer report what the annotation records?
  What counts as "what the annotation records" is whichever line of
  [GROUND_TRUTH] begins "recorded" -- the field name varies with the dataset. It
  may be an error type, a count of error episodes and their duration, a graded
  severity in words, an action, ratings with their descriptions, or the state
  of a safety criterion. Grade against what is there, never against what you
  expect to be there. Grade WHAT happened here; WHEN it happened is graded
  under localization_score, so a missing or wrong time does not lower this
  score.
  The model saw the video, so it may describe things the annotation neither
  records nor rules out: bleeding on a dataset that does not record bleeding,
  an instrument's position, how tissue looks. Before scoring label_score, set
  aside every claim about a property that [GROUND_TRUTH] lists as not
  recorded, and grade what is left against the recorded line. Such a claim
  cannot contradict the annotation, so it never makes label_score 1; it is
  graded under scope_score. This holds when the question itself asks about
  that property: an answer that discusses hemostasis because the question
  asked for it is answering the question.
  5  everything recorded is reported, in any reasonable wording. Judge the
     meaning, not the words.
  4  everything recorded is reported and correct, but one part is stated loosely
     enough that a reader has to infer it, without any recorded value changing.
     A 4 is never given when a severity, a rating level, a step or a kind of
     event differs from the block, however close: that is a 1.
  3  part of what was recorded is reported and part omitted -- one of two error
     types, or the fact of an error without the recorded episode count or
     duration when those are the only properties recorded.
  2  only the bare existence of the finding is reported, when the annotation
     records more about it, and nothing reported is wrong.
  1  nothing recorded is reported, or the answer contradicts the annotation: a
     different kind of event than the one recorded, an event the annotation
     records said not to have happened, a recorded state stated as its
     opposite, or the opposite verdict to the one the recorded state supports.
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

localization_score (1-5) -- does the answer name the right occurrences, and when?
  Some datasets have nothing here to grade: where the question itself names the
  unit, or the block says localization is NOT APPLICABLE, return null and list
  "localization_score" in "uncertain". Do not invent a localization judgement out
  of a question that already contains it. Where the question asks when
  something happens and [GROUND_TRUTH] gives its time, an answer that gives no
  time, or a different time under the rule above, scores 1.
  5  exactly the faulty occurrences, by ordinal ("the second and third"), by an
     all-form when every occurrence is faulty ("all three transfers"), by index,
     or by a time that is the recorded time under the rule above.
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
     however short it is. A visual claim the annotation cannot confirm (bleeding,
     an instrument's position, tissue appearance) counts here, and only here.

Where you cannot judge a domain from what you were given, list that domain's
name in "uncertain" and return null for it. An abstention is more useful than a
guessed score.

[GROUND_TRUTH]
  setting: <bench-top training task | in-vivo procedure>
  dataset: <name>
  recorded ...: <what the annotation records for this record>
  not recorded in this dataset: <properties the annotation does not carry>
  source annotation: <file or dataset>
  what the answering model was shown: <frames, stretch of the recording, spacing>

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
