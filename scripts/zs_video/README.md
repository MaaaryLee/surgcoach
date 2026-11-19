# Zero-shot video QA

Each model gets **the question and frames from the video**, and nothing from the annotations.
A judge model from a different family then scores each answer against the annotation, 1-5.
Each model is tested once, at one setting (Yulu, 11-17).

## What a model sees

- **System prompt:** [`vlm-zero-shot-system-prompt.md`](../../Prompts_And_Pipeline/vlm-zero-shot-system-prompt.md).
  It asks for plain prose, at most five sentences, with times written as in the frame labels.
- **User turn:**
  1. One line naming the procedure or task, for example "This is a recording of a laparoscopic
     cholecystectomy."
  2. The frames in order, each right after its time in the recording, for example `[14:41]`.
  3. The question, word for word as in the dataset.
- **Frames:** one per second inside the question's stretch of video. If that is more than 64,
  64 are spread evenly. The longest side is at most 640 px. Every model gets the same frames.

## Which stretch of video each question gets

| Questions | Stretch |
|---|---|
| JIGSAWS VTS locate | the whole recording |
| JIGSAWS D1, D2; MB140 D1, D2, D5 | the span the question names |
| JIGSAWS D5 | the whole recording |
| MB140 B1 urgent concern, B4 near miss | the event, with 30 s either side |
| MB140 B5, B6 when to pause | 180 s holding the event, which starts at a random (seeded) point |
| MB140 B3 rank risks | the whole operation |
| Endoscapes B1, B2, B3, B7 | the 30 s up to and including the keyframe |
| Endoscapes B5 when to pause | 20 s either side of the keyframe |

The B5/B6 offset is random so that the answer's time can't be read off the window's position.

## Models and settings

| `MODEL` | Weights (in `/mnt/opr/mairuili/models`) | Sampling |
|---|---|---|
| `qwen38-27b` | `Qwen3.8-27B-Q4_K_M.gguf` + `mmproj-Qwen3.8-27B-BF16.gguf` (ggml-org) | thinking on, temperature 0.6, top-p 0.95, top-k 20, up to 8000 tokens |
| `qwen3vl-8b` | `Qwen3VL-8B-Instruct-Q8_0.gguf` + `mmproj-Qwen3VL-8B-Instruct-F16.gguf` (Qwen's own GGUF) | temperature 0.7, top-p 0.8, top-k 20, presence penalty 1.5, up to 2048 tokens |
| `judge` | `gemma-3-27b-it-Q4_K_M.gguf` (ggml-org), text only | temperature 0 |
| `judge-oss` | `gpt-oss-120b-MXFP4.gguf` (ggml-org, 63 GB, two GPUs: submit with `--gres=gpu:2`), text only | temperature 1.0, top-p 1.0 (OpenAI's recommendation), reasoning effort medium |

All of them run on llama.cpp's `llama-server` (the build in `2026-07-29-d75-thinking/tools`).

The judge prompt is [`vlm-zero-shot-judge-v1.md`](../../Prompts_And_Pipeline/vlm-zero-shot-judge-v1.md),
which is rubric v5 with five changes listed at its top, and known limits listed there (for
example, it is too harsh on a correct Endoscapes "not safe" verdict that also describes
bleeding). The report adds checks that need no judge:

- **Time in the recorded span**, where the question asks when and the annotation records one
  span (JIGSAWS VTS, MB140 B5 and B6). It checks whether the answer's first time or span falls
  inside or overlaps the recorded span, give or take one frame spacing. That is the rubric's
  localization 5. The judges were not reliable on it: both scored 1 for some answers that pass.
  MB140 D2 records several events, so its row keeps the judge's localization. Endoscapes B5 asks
  when, but its annotation records no time, so it has no time score.
- **Time overlap (IoU)** of the answer's first span with the recorded JIGSAWS VTS span.
- **The yes/no** against the recorded CVS state for Endoscapes B7.

Where the judge's localization is used, an answer with no time scores 1, as the rubric says
(the judge tends to give those 5). A question the model did not answer (for example, its
thinking ran out of tokens on all three tries) counts as 1 and as a miss on the time check.

## Running it on oprime

```bash
# 1. video units + frames, from the 11-09 run's units (CPU only)
python3 -I scripts/zs_video/prep_video_units.py --units <11-09 run>/units/*.jsonl --out . --workers 8
# 2. answers, one job per model
MODEL=qwen38-27b sbatch --chdir=$PWD scripts/zs_video/zs_video.sbatch
MODEL=qwen3vl-8b sbatch --chdir=$PWD scripts/zs_video/zs_video.sbatch
# 3. judge, after both
MODEL=judge sbatch --chdir=$PWD --dependency=afterok:<id1>:<id2> scripts/zs_video/zs_video.sbatch
# 4. the table
python3 -I scripts/zs_video/zs_video_report.py . report
```

`LIMIT=2` runs the first two questions of each type, for a smoke test. A job that stops can be
resubmitted: it skips the questions already answered or judged.

A slow model can share one list of questions over several GPUs: give each job the same `UNITS`
and `CLAIM_DIR`. Each question goes to the first job that reaches it, each job writes
`outputs/<model>/pool-<job id>/`, and questions any folder of that model has already answered
are skipped. List the slowest question types first so the jobs finish together:

```bash
MODEL=qwen38-27b UNITS="video_units/MultiBypass140_B6.jsonl ..." CLAIM_DIR=claims/qwen38-27b \
  sbatch --chdir=$PWD scripts/zs_video/zs_video.sbatch     # once per GPU
```

To try a new judge, score it first on answers whose right label is known: set
`CHECK=judge_check.jsonl` (and `CHECK_COMPARE=<another judge's id>` to score that judge's stored
reviews too) on the judge job. `judge_check.py` then runs before the full judging, and the job
log shows how many labels fall in range.

If a pool job is cancelled, the questions it was working on stay claimed. Remove their files
from the claim dir (those with no answer in any `generations.jsonl`) before resubmitting.
