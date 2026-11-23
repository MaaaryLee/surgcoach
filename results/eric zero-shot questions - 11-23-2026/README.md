# Eric's zero-shot questions, 11-23-2026

The 1,069 questions in Eric's balanced zero-shot set (11-18), in the same layout as
Mairui's full QA dataset (11-22), so we can split the models between us and each run both sets.

## Questions

| Dataset | Type | Asks | Questions | Videos | Time scored | Balance | File |
|---|---|---|---:|---:|---|---|---|
| MultiBypass140 | FIND | when a phase takes place in the operation | 100 | 76 | yes |  | [jsonl](qa/MultiBypass140_FIND.jsonl) |
| MultiBypass140 | AE | did an adverse event happen in the clip; what, when, how serious (yes/no) | 100 | 71 | yes | 50 yes / 50 no | [jsonl](qa/MultiBypass140_AE.jsonl) |
| MultiBypass140 | NEXT | which step comes next after the clip | 100 | 72 | no |  | [jsonl](qa/MultiBypass140_NEXT.jsonl) |
| CholecT50 | FIND | when the surgeon does an action to a target | 534 | 50 | yes |  | [jsonl](qa/CholecT50_FIND.jsonl) |
| Cholec80 | FIND | when a tool first comes into view | 30 | 30 | yes |  | [jsonl](qa/Cholec80_FIND.jsonl) |
| JIGSAWS | MISTAKE | was there a mistake during a gesture, and which occasion (yes/no) | 136 | 58 | no | 68 yes / 68 no | [jsonl](qa/JIGSAWS_MISTAKE.jsonl) |
| AIxSuture | D1 | coaching feedback on the whole trial | 69 | 69 | no | 23 low / 23 average / 23 high | [jsonl](qa/AIxSuture_D1.jsonl) |
| | | | **1,069** | | | | |

Videos: 336 in all (AIxSuture 69, Cholec80 30, CholecT50 50, JIGSAWS 58, MultiBypass140 129). [`videos.csv`](videos.csv) lists each one and how
many questions use it.

## Balance

- **MultiBypass140 AE:** 50 clips with a recorded adverse event, 50 without.
- **JIGSAWS MISTAKE:** every question with a recorded mistake (68), and 68 without.
- **AIxSuture D1:** 23 trials per overall rating band: low (1-2), average (3), high (4-5).
- The FIND and NEXT types have no yes/no answer. Every FIND question's target is in the video.

## Video input

- `video.start_s` / `video.end_s` give the stretch to show, in seconds. `end_s` is null when
  the whole recording is shown.
- `times_count_from`: MultiBypass140 AE and NEXT show a clip, and their answers count time
  from the clip's start. Every other type shows the whole recording, and its answers count
  from the recording's start.
- `oprime_path` is where the video is on oprime (`/mnt/sun/shared/datasets/surgical_skill`), and
  `sunflower_path` the same file on sunflower (`/data/shared/...`). CholecT50 is a folder of
  frames at 1 fps, not a video file.
- The 11-18 runs used each model's default video settings, at most 2 fps and 768 frames.

## Fields

- `unit_id`, `dataset`, `template_id`, `question`, `video` as above.
- `correct_answer`: "Yes" or "No", for the AE and MISTAKE types only.
- `ground_truth.reference_answer`: the answer the judge compares against.
- `ground_truth.recorded`: what the annotation records, as given to the question writer.
- `ground_truth.localization`: whether the answer's time is scored.
- `ground_truth.median_scores`: AIxSuture only, the raters' median scores.

Built by [`scripts/export_zeroshot_questions.py`](../../scripts/export_zeroshot_questions.py).
