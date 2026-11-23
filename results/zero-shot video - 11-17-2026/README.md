# Zero-shot video QA, 11-17-2026

Two vision-language models answered the same 741 questions from video frames alone, with no
annotations and no examples.

Every question that qualifies, with each yes/no type balanced, is in
[full QA dataset - 11-22-2026](../full%20QA%20dataset%20-%2011-22-2026/README.md).

## Questions

Each file holds every question of one type. For each question it gives:

- the stretch of video the models saw;
- the question;
- what the annotation records, word for word as the judge got it;
- both models' answers.

The `.md` files are for reading. The `.jsonl` files hold the same content for code.

| Dataset | Type | Asks | Questions | Files |
|---|---|---|---|---|
| JIGSAWS | D1 | feedback on a stretch of the trial | 50 | [read](qa/JIGSAWS_D1.md) · [jsonl](qa/JIGSAWS_D1.jsonl) |
| JIGSAWS | D2 | which skill area to improve first | 20 | [read](qa/JIGSAWS_D2.md) · [jsonl](qa/JIGSAWS_D2.jsonl) |
| JIGSAWS | D5 | the strongest technical skill | 5 | [read](qa/JIGSAWS_D5.md) · [jsonl](qa/JIGSAWS_D5.jsonl) |
| JIGSAWS | VTS | when something happens in the recording | 50 | [read](qa/JIGSAWS_VTS.md) · [jsonl](qa/JIGSAWS_VTS.jsonl) |
| MultiBypass140 | B1 | the most urgent safety concern at a given time | 50 | [read](qa/MultiBypass140_B1.md) · [jsonl](qa/MultiBypass140_B1.jsonl) |
| MultiBypass140 | B3 | rank the risks over the whole operation | 50 | [read](qa/MultiBypass140_B3.md) · [jsonl](qa/MultiBypass140_B3.jsonl) |
| MultiBypass140 | B4 | whether a clip shows a near miss | 50 | [read](qa/MultiBypass140_B4.md) · [jsonl](qa/MultiBypass140_B4.jsonl) |
| MultiBypass140 | B5 | when to pause the trainee to prevent harm | 50 | [read](qa/MultiBypass140_B5.md) · [jsonl](qa/MultiBypass140_B5.jsonl) |
| MultiBypass140 | B6 | when to pause the trainee for coaching | 50 | [read](qa/MultiBypass140_B6.md) · [jsonl](qa/MultiBypass140_B6.jsonl) |
| MultiBypass140 | D1 | feedback on a stretch of the operation | 50 | [read](qa/MultiBypass140_D1.md) · [jsonl](qa/MultiBypass140_D1.jsonl) |
| MultiBypass140 | D2 | which adverse event to address first | 31 | [read](qa/MultiBypass140_D2.md) · [jsonl](qa/MultiBypass140_D2.jsonl) |
| MultiBypass140 | D5 | which phase went best | 35 | [read](qa/MultiBypass140_D5.md) · [jsonl](qa/MultiBypass140_D5.jsonl) |
| Endoscapes2023 | B1 | the most urgent safety concern at a frame | 50 | [read](qa/Endoscapes2023_B1.md) · [jsonl](qa/Endoscapes2023_B1.jsonl) |
| Endoscapes2023 | B2 | whether it is safe to keep dissecting toward clipping | 50 | [read](qa/Endoscapes2023_B2.md) · [jsonl](qa/Endoscapes2023_B2.jsonl) |
| Endoscapes2023 | B3 | rank the risks | 50 | [read](qa/Endoscapes2023_B3.md) · [jsonl](qa/Endoscapes2023_B3.jsonl) |
| Endoscapes2023 | B5 | when to pause the trainee to prevent harm | 50 | [read](qa/Endoscapes2023_B5.md) · [jsonl](qa/Endoscapes2023_B5.jsonl) |
| Endoscapes2023 | B7 | whether it is safe to clip or cut | 50 | [read](qa/Endoscapes2023_B7.md) · [jsonl](qa/Endoscapes2023_B7.jsonl) |

## Setup: one setting per model

- **Questions:** the 741 of the 11-09 run (branch `qwen38-zero-shot`): 50 per question type,
  or every eligible question where there are fewer than 50.
- **Video:** one frame per second over the question's stretch of the recording, at most 64
  spread evenly, longest side 640 px. Each frame is labelled with its time in the recording,
  and every model gets the same frames. The stretch for each question type is listed in
  [`scripts/zs_video/README.md`](../../scripts/zs_video/README.md).
- **System prompt:** [`vlm-zero-shot-system-prompt.md`](../../Prompts_And_Pipeline/vlm-zero-shot-system-prompt.md).
- **Qwen3.8-27B:** Q4_K_M with its vision encoder, thinking on, temperature 0.6, up to 8,000
  tokens.
- **Qwen3-VL-8B-Instruct:** Q8_0, temperature 0.7, up to 2,048 tokens.

Both ran on llama.cpp on oprime.

## Reading the answers

- **No answer.** On 21 questions Qwen3.8 spent its whole 8,000-token thinking budget on all
  three tries and never wrote an answer: 14 of the 50 VTS questions, 6 MB140 B5 and 1
  Endoscapes B5.
- **Wording hints in the recorded text.** For the D questions of both JIGSAWS and
  MultiBypass140, the recorded text still holds hints written for the 11-09 text-only run on
  how to word an answer. Examples: "Say it as: ...", "in the answer, name it ...", "Open with
  this verdict ...". They are not findings, and the judge got them too.
- **Yes/no balance.** Endoscapes B7 has 19 "Yes" and 31 "No" correct answers, and Endoscapes
  B2 and MultiBypass140 B4 are "No" for all 50. So a model that always says "No" scores well
  on all three. The full dataset balances B7 and leaves out B2 and B4.
- **Question design.** Some questions can't be answered correctly as asked:
  - Endoscapes B1, B3 and B5 never mention the critical view of safety, yet the recorded
    answer is the state of its three criteria.
  - MultiBypass140 B4 asks about a near miss, but every clip holds a recorded actual injury.

## Scores

The judge is gpt-oss-120b (MXFP4, text only, reasoning effort medium, temperature 1.0). It uses
[`vlm-zero-shot-judge-v1.md`](../../Prompts_And_Pipeline/vlm-zero-shot-judge-v1.md) and scores
each answer 1-5 against the recorded annotation. Its scores are still on oprime and are not in
this folder yet.

[`judge_check.jsonl`](judge_check.jsonl) holds 37 of these answers, each with the label range a
correct judge should give. Claude read them against the annotation; no surgeon checked them.
[`scripts/zs_video/judge_check.py`](../../scripts/zs_video/judge_check.py) scores any judge on
them.

Code: [`scripts/zs_video/`](../../scripts/zs_video/), with its own README. Run folder on oprime:
`/mnt/opr/mairuili/surgcoach_runs/2026-11-17-zs-video`.
