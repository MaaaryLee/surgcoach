# Results

Generated QA output that is worth keeping. Only runs that represent a real step
forward belong here, and any rule violation a run still contains is named below
rather than left for a reader to discover. Superseded runs are not committed even
for reference -- the failure is described in the commit message instead of being
archived as data.

Subdirectories are named `outputs after <what was fixed> - M-D-YYYY`, so the
folder itself records which change the batch is demonstrating.

Every record is annotation-only: no frame, image, or video is ever passed to
the model. Full provenance (exact prompt, raw model output including its
reasoning, source annotation values, validation status) is in
`qa_records.jsonl`, which is the source of truth. `qa_pairs.jsonl` and
`qa_pairs.md` are extracted views for machines and humans respectively;
regenerate them with `python3 scripts/extract_qa_pairs.py <dir>/qa_records.jsonl`.

Check a batch with `python3 scripts/check_template_d.py <dir>`.

## `outputs after stratified trial selection - 7-29-2026`

Template D, all five templates, across all three JIGSAWS tasks: 75 records from
5 trials each of Suturing, Knot Tying and Needle Passing, one question per whole
video.

| | |
| --- | --- |
| Model | `qwen3.6-35b-a3b-iq4xs` (4-bit GGUF) via local Ollama |
| Thinking | requested explicitly; every record carries a reasoning block, ~89% of generated text |
| Sampling | temperature 0.7, `max_new_tokens` 9000, `num_ctx` 12288, up to 3 retries |
| Trials | stratified across each task's GRS range -- see `scripts/stratified_trials.ps1` |
| Coverage | GRS 6-30; 25 expert, 25 intermediate, 25 novice records |
| Validation | 75 records, **75 valid, 0 rejected**, 6 records needed a retry |
| Checker | **1 issue, listed below and accepted** |

### What changed: the trials are a sample now, not the front of the alphabet

Every earlier batch used B001-B004 plus C001 for each task, which is simply the
first entries in the file listing. That selection left 13 of 15 trials at 17 or
below out of 30, made all five Suturing trials novice, and included none of the
29 expert trials in the dataset. Two templates cannot function on a sample like
that, and both looked broken as a result:

- **C7** asks which of five supervision levels a trainee needs. Every trial
  needed heavy supervision, so it answered "repeated verbal cueing" for all 15
  records. On the extremes of the real range it discriminates correctly -- GRS 30
  returns "Independent", and an expert scoring 8 returns "Hands-on guidance".
- **D5** asks for the strongest technical skill. No trial had a strength, so it
  kept answering that nothing had reached one.

The replacement spans GRS 6 to 30 and lands on 25 records per skill level. It
also decouples `skill_level` from `grs_total`, which the old set could not:
`Suturing_D004` is an expert scoring 8 and `Knot_Tying_H004` a novice scoring 22.
Those cases test whether the model reasons from the scores or repeats the
self-reported label -- and C7 gets them right, choosing from the score.

Two prompt changes came with it. C7's instruction previously offered one worked
example, at the bottom of the range, which likely anchored it there; it now states
the scale's span and requires one of the five level names verbatim. And thinking
is now requested explicitly rather than relied on as a model default that an
update could silently drop.

### The known issue in this batch

Left in place rather than regenerated. Re-sampling a record because the checker
dislikes its content selects for output that passes the checker, which would make
the reported quality better than the pipeline's actual quality.

**`D5 Suturing_H001` -- meta-reference.** Writes "A score of three indicates that
while you maintain reasonable progression between steps...", and hard rule 11
bars naming a score in the answer at all; a reader who cannot see the scores has
no way to judge it. Worth noting the checker only caught this because the pattern
was broadened an hour earlier -- it previously required "your score" or "the
score", so "A score of three" passed.

**Rerunning does not clear this class.** Four runs of this configuration produced
one to two content issues each, landing in different records every time, and
every violation was already explicitly banned in the prompt. At temperature 0.7
that residual is sampling variance rather than a missing rule, so the honest
figure is roughly 1-2 per 75, not 0.

### What changed: coaching vocabulary is tied to the rubric

JIGSAWS ships only element names and 1-5 numbers -- its `readme.txt` documents the
meta file as "score of each element of modified global rating score" and gives no
descriptive text at all. That left an unanswered question the earlier batches got
wrong in both directions: how specific may coaching be before it is inventing a
cause for a number?

The answer comes from the OSATS global rating scale that JIGSAWS' modified GRS is
adapted from, whose score-1 anchors are explicit:

| Element | Score-1 anchor | Vocabulary this grounds |
| --- | --- | --- |
| Respect for tissue | "Frequently used unnecessary force on tissue or caused damage by inappropriate use of instruments" | force, pressure, grip, damage |
| Suture/needle handling | "Repeatedly makes tentative or awkward moves with instruments" | tentative, awkward, stiff, hesitant, fumbling |
| Time and motion | "Many unnecessary moves" | wasted motion, redundant travel, backtracking |
| Flow of operation | "Frequently stopped operating and seemed unsure of next move" | pauses, stopping, unsure of the next step |

Both the prompt and the checker now use these anchors, which replaces
case-by-case judgement with one rule: **an element's own anchor vocabulary is
grounded when that element scores low, and ruled out when it scores high.**
Naming a mechanic no anchor mentions -- wrist angle, tip control, tremor --
invents a cause for a number and is barred outright.

At a score of 4 the trainee sits between the score-3 anchor ("occasionally stiff
or awkward") and score-5 ("no awkwardness"), so a hedged mention such as "minor
residual awkwardness" is correct interpolation and is allowed; unhedged criticism
at 4 is not, and at 5 the anchor denies the trait outright.

What this batch demonstrates, in the order the problems were found:

1. **Self-contained questions.** D2/D3/D4 used to say "among these three feedback
   points" without naming them. The three points are computed in code from the
   three lowest GRS subscores with deterministic tie-breaking, and substituted
   into the question text.
2. **No hidden-rubric references.** Answers used to say "this received the lowest
   rating on your technical rubric," which a reader who cannot see the scores has
   no way to judge. Score references now live only in the rationale.
3. **Natural coaching voice.** Every answer once opened with the same sentence.
   Fixed with a hard rule plus a mechanism threading a batch's already-used
   opening sentences into later prompts -- each record is an independent model
   call with no memory of the others, so a static prompt alone cannot prevent
   repetition.
4. **No fabricated behaviour.** Answers invented specifics the annotations cannot
   support ("you are applying unnecessary downward pressure"). Rule 1 states that
   forward-looking phrasing is not an exemption, and now also covers
   presupposition -- telling a trainee to *stop* doing something asserts they are
   doing it.
5. **Anchor-tied specificity**, as above. This replaced a paraphrase in the prompt
   that was wrong: `time_and_motion` was described as measuring "fluency", where
   its anchor reads "many unnecessary moves".
6. **No invented patient anatomy or outcomes.** JIGSAWS is a bench-top exercise,
   so rule 4 bars fascia, wound margins and postoperative periods -- and now also
   perfusion, ischaemia, necrosis, bleeding and healing, after a record wrote
   "excessive tension can lead to tissue ischemia" about a synthetic pad that has
   no blood supply.
7. **Skill level glossed.** The prompt says `skill_level: I (intermediate, 10-100
   hours robotic surgical practice)` rather than a bare `I`. 58% of Template D
   records reason about skill level, and an unglossed code had produced an answer
   calling level I "novice".

### Correction to the previous entry

The entry for the 7-26-2026 batch claimed "75/75 valid, 0 rejected, 0 retries
fired, 0 issues". The validity and retry figures were accurate; **the zero-issues
claim was not.** That batch contains records asserting `your wrist control becomes
erratic` and `quiet the tremors in your wrist` -- mechanics no element measures --
and one coaching excessive force against a `respect_for_tissue` of 4/5, where the
anchor records appropriate handling.

The checker reported zero because it had no test for any of that yet. The number
was true of the checker and false of the data, and it was published as though it
described the data. **Every count in this file should be read as *no known failure
mode fired*, never as *the output is correct*.**

### Known limitations

- **The checker only finds what it has been taught to find.** It is pattern
  matching over failure modes observed so far. Several violations were found by
  reading answers, not by the checker, and adding those patterns then surfaced
  more. Expect the same again.
- **Rare failures are observed, not measured.** A perfusion violation occurred
  once in 75 records before rule 4 was extended. At that rate a clean batch of 75
  is roughly what you would expect *even with no fix at all*, so that fix is not
  confirmed -- only that specific sentence is gone. Confirming it needs a larger
  batch than this hardware can produce in reasonable time.
- **One row of the anchor table is inferred.** JIGSAWS does not publish its own
  wording for "Suture/needle handling"; that row comes from its OSATS ancestor,
  "Instrument handling". The other three are verbatim.
- **Generated on a 4-bit quantisation, not full precision.** The model is 18 GB
  against 12 GB of VRAM, so ~43% of layers run on CPU and `num_ctx` is capped at
  12288. This batch lost no records to truncation, but the margin is thin rather
  than comfortable: reasoning is ~89% of generated text, and the headroom that
  fixed it came from shortening the prompt, not from having room to spare. Whether
  4-bit costs anything in coaching quality is untested and needs the cluster.
- **The prompt budget is a ceiling, not a guideline.** Adding rules now competes
  directly with the model's reasoning for the same context window. The runner
  refuses to start above 34,000 assembled characters; the current prompt is 30,599.
- **Nothing verifies that the coaching is clinically sound.** The checker
  confirms records are grounded in the labels and free of the violations it knows
  about; it cannot judge medicine. Expert review is required.
- **Type C is not in this batch.** C1/C2/C3/C6/C7 are supported and pass cleanly
  (30/30 valid, 0 issues on a sample spanning GRS 7-26), but have not been
  generated at scale. C4 and C5 are refused: bimanual dexterity and depth
  perception are GEARS/GOALS domains, and JIGSAWS scores a modified OSATS that
  does not include them.
