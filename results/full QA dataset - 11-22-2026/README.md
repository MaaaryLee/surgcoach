# Full QA dataset, 11-22-2026

Every question that qualifies for 15 of the 17 question types in the 11-17 zero-shot video
test: 9,662 questions, without model answers. Each yes/no type has as many "yes" as "no"
correct answers.

## Questions

| Dataset | Type | Asks | Questions | Videos | Asked on 11-17 | File |
|---|---|---|---:|---:|---:|---|
| JIGSAWS | D1 | feedback on a stretch of the trial | 56 | 56 | 50 | [jsonl](qa/JIGSAWS_D1.jsonl) |
| JIGSAWS | D2 | which skill area to improve first | 20 | 20 | 20 | [jsonl](qa/JIGSAWS_D2.jsonl) |
| JIGSAWS | D5 | the strongest technical skill | 5 | 5 | 5 | [jsonl](qa/JIGSAWS_D5.jsonl) |
| JIGSAWS | VTS | when something happens in the recording | 628 | 92 | 50 | [jsonl](qa/JIGSAWS_VTS.jsonl) |
| MultiBypass140 | B1 | the most urgent safety concern at a given time | 797 | 138 | 50 | [jsonl](qa/MultiBypass140_B1.jsonl) |
| MultiBypass140 | B3 | rank the risks over the whole operation | 54 | 54 | 50 | [jsonl](qa/MultiBypass140_B3.jsonl) |
| MultiBypass140 | B5 | when to pause the trainee to prevent harm | 797 | 138 | 50 | [jsonl](qa/MultiBypass140_B5.jsonl) |
| MultiBypass140 | B6 | when to pause the trainee for coaching | 797 | 138 | 50 | [jsonl](qa/MultiBypass140_B6.jsonl) |
| MultiBypass140 | D1 | feedback on a stretch of the operation | 423 | 138 | 50 | [jsonl](qa/MultiBypass140_D1.jsonl) |
| MultiBypass140 | D2 | which adverse event to address first | 31 | 31 | 31 | [jsonl](qa/MultiBypass140_D2.jsonl) |
| MultiBypass140 | D5 | which phase went best | 53 | 53 | 35 | [jsonl](qa/MultiBypass140_D5.jsonl) |
| Endoscapes2023 | B1 | the most urgent safety concern at a frame | 1,906 | 201 | 50 | [jsonl](qa/Endoscapes2023_B1.jsonl) |
| Endoscapes2023 | B3 | rank the risks | 839 | 168 | 50 | [jsonl](qa/Endoscapes2023_B3.jsonl) |
| Endoscapes2023 | B5 | when to pause the trainee to prevent harm | 1,906 | 201 | 50 | [jsonl](qa/Endoscapes2023_B5.jsonl) |
| Endoscapes2023 | B7 | is it safe to clip or cut? (yes/no) | 1,350 | 201 | 50 | [jsonl](qa/Endoscapes2023_B7.jsonl) |
| | | | **9,662** | | **641** | |

"Asked on 11-17" counts the questions that were in the 11-17 zero-shot video test. Those
questions are identical here: same wording, recorded annotation and video window.

## Yes/no balance

Endoscapes B7 is the one yes/no type: 675 "Yes" and 675 "No".

- **Rule.** The answer is "Yes" when all three critical-view-of-safety criteria are achieved,
  otherwise "No". A criterion counts as achieved when at least 2 of its 3 raters marked it.
  This is the template's own rule.
- **Which frames.** 675 of the 11,090 rated keyframes are safe by this rule, from 61 of the
  201 videos, and all of them are in. The 675 unsafe frames are the first in the 11-09
  sampling order, which takes one frame per video before any video repeats and rotates over
  the criteria patterns. They come from 184 videos.
- **11-17 run.** The 50 B7 questions asked on 11-17 are all here. They were 19 "Yes" and
  31 "No", so that run's B7 scores rest on an unbalanced set.

Two yes/no types are left out, because their correct answer is "No" for every question:

- **Endoscapes B2** asks whether it is safe to keep dissecting toward clipping, judged on
  bleeding control, tissue plane, instrument position and energy use. None of these is
  labelled, so the recorded answer can never be "Yes".
- **MultiBypass140 B4** asks whether a clip shows a near miss. MultiBypass140 labels only
  adverse events that happened, not near misses.

Their 11-17 questions and answers stay in the 11-17 folder. New wording for them is Yulu's
call.

## Which questions qualify

The rules are those of the 11-09 question builders, without the 50-per-type cap.

- **MultiBypass140 B1, B5, B6:** every recorded adverse event with a severity grade.
- **MultiBypass140 B3:** every operation with at least two kinds of adverse event.
- **MultiBypass140 D1:** every phase in which 1 to 5 adverse events began (events recorded
  together count as one), with no event running on from the phase before.
- **MultiBypass140 D2:** such phases with 2 to 6 events, exactly one of them at grade 4 or 5.
- **MultiBypass140 D5:** every operation in which exactly one commonly eventful phase has
  nothing above grade 2.
- **MultiBypass140 D, all three:** the 11-17 run took one question per operation; here every
  candidate is in.
- **Endoscapes B1, B5:** every keyframe with object boxes (the 1,933 in the dataset's COCO
  splits) where at least one criterion was not marked achieved by all three raters.
- **Endoscapes B3:** those keyframes with at least two such criteria, whose three criteria
  are not all rated the same.
- **Endoscapes B7:** all 11,090 rated keyframes, balanced as above.
- **JIGSAWS D1:** every trial whose overall performance is rated 1, 3 or 5.
- **JIGSAWS D2:** every trial where one skill area alone is rated lowest, at 1 or 3.
- **JIGSAWS D5:** every trial where one skill area alone is rated 5.
- **JIGSAWS VTS:** all 628 reviewed locate questions (blind-filtered and audited, 10-25).
  One of them has a blank question in the review file. Its question is taken from the
  batch record its answer was written for.

The video window of each question follows the 11-17 rules, listed in
[`scripts/zs_video/README.md`](../../scripts/zs_video/README.md).

## Fields

Each line of a `qa/<dataset>_<type>.jsonl` file is one question:

- `unit_id`, `dataset`, `template_id`, `question`.
- `video`:
  - `id` is the JIGSAWS trial, MultiBypass140 operation or Endoscapes video.
  - `start_s`, `end_s` and `window` give the stretch of the recording to show, in seconds
    and as clock times.
  - `rule` says how that stretch was chosen.
  - `keyframe` (Endoscapes only) is the frame number at 25 fps. The image is
    `<id>_<keyframe>.jpg`.
- `correct_answer`: "Yes" or "No", Endoscapes B7 only.
- `ground_truth` has what the judge compares an answer against:
  - `recorded` is what the annotation records, word for word as the judge gets it.
  - `not_recorded` is what it does not record.
  - `recorded_label` describes the labels.
  - `localization` says whether the answer's time is scored.
  - `setting` and `source_annotation` give the context and the source.
- `in_11_17_run`: whether the question was in the 11-17 test.

[`summary.json`](summary.json) gives each type's count, eligible pool and rule.

## Sources

- **JIGSAWS** (Gao et al. 2014): ratings, gesture transcriptions and kinematics.
- **UVA-DSA executional error labels** for JIGSAWS.
- **MultiBypass140** (CAMMA): adverse event, phase and step labels, CC BY-NC-SA 4.0.
- **Endoscapes2023** (CAMMA): critical-view-of-safety ratings and object boxes,
  CC BY-NC-SA 4.0.

## Code

[`scripts/full_qa/`](../../scripts/full_qa/) builds the set on oprime:

- It uses the 11-09 builders (branch `qwen38-zero-shot`, in its results folder's `scripts/`).
- It uses the 11-17 window code, [`scripts/zs_video/prep_video_units.py`](../../scripts/zs_video/prep_video_units.py).
- It checks that every question asked on 11-17 comes out unchanged.

Run folder: `/mnt/opr/mairuili/surgcoach_runs/2026-11-22-full-qa`.
