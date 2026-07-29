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

## `outputs after vision wording stripped from prompt - 7-29-2026`

Template D, all five templates, across all three JIGSAWS tasks: 75 records from
5 trials each of Suturing, Knot Tying and Needle Passing, one question per whole
video.

| | |
| --- | --- |
| Model | `qwen3.6-35b-a3b-iq4xs` (4-bit GGUF) via local Ollama |
| Thinking | enabled -- every record carries a reasoning block, ~89% of generated text |
| Sampling | temperature 0.7, `max_new_tokens` 9000, `num_ctx` 12288, up to 3 retries |
| Validation | 75 records, **75 valid, 0 rejected**, 5 records needed a retry |
| Checker | **2 issues, both listed below and both accepted** |

### What changed since the previous batch

The system prompt was pasting the whole of `template-questions-A-D.md` into
itself, blockquotes included -- and those blockquotes hold the canonical *vision*
wording, which asks for "visible evidence" and a numeric score. So the model was
being told to supply visible evidence in the same prompt that forbids inventing
observations, and ~4,700 characters of the context window were carrying
instructions written for a different pipeline. `load_question_templates` had
always stripped blockquotes when reading question text; the include did not.

Stripping them there too took the assembled prompt from 33,326 to 29,991
characters. That fixed the truncation as a side effect: the model's reasoning is
about 89% of what it generates, so it needs roughly 4,000 tokens of headroom
before it writes any JSON, and the extra room took rejections from 1 to 0 and
retries from 17 to 5.

`scripts/run_annotation_qa_jigsaws.py` now also refuses to start if the assembled
prompt exceeds a stated budget. Prompt growth is silent otherwise -- it truncates
records rather than failing -- and one 19% overrun cost 11 of 75 records.

### The two known issues in this batch

Both are left in place rather than regenerated. Re-sampling a record because the
checker dislikes its content selects for output that passes the checker, which
would make the reported quality better than the pipeline's actual quality.

1. **`D4 Suturing_B002` -- banned phrase.** Writes "the trainee should return to
   basic peg transfer", which hard rule 13 forbids. The same answer also asserts
   "hesitant wrist rotations", and wrist mechanics appear in no GRS anchor, so
   this record has a second problem the checker does not yet detect.
2. **`D3 Knot_Tying_C001` -- meta-reference.** Writes "A midpoint assessment
   shows your current technique works", and hard rule 11 bars referring to the
   existence of an assessment at all; a reader who cannot see the scores has no
   way to judge such a claim.

**Rerunning does not clear this.** Three runs of the identical configuration each
produced about two content issues, landing in different records every time, and
every violation was already explicitly banned in the prompt. At temperature 0.7
that residual is sampling variance, not a gap in the rules, so the honest number
is roughly 2 per 75 rather than 0.

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
  refuses to start above 34,000 assembled characters; the current prompt is 29,991.
- **Nothing verifies that the coaching is clinically sound.** The checker
  confirms records are grounded in the labels and free of the violations it knows
  about; it cannot judge medicine. Expert review is required.
- **Type C is not in this batch.** C1/C2/C3/C6/C7 are supported and pass cleanly
  (30/30 valid, 0 issues on a sample spanning GRS 7-26), but have not been
  generated at scale. C4 and C5 are refused: bimanual dexterity and depth
  perception are GEARS/GOALS domains, and JIGSAWS scores a modified OSATS that
  does not include them.
