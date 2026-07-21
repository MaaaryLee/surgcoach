# SurgCoach

Structured prompt and evaluation materials for surgical coaching QA on intraoperative video.

## New Here?

Start with [QUICKSTART.md](QUICKSTART.md) — a from-zero guide to a first mock run, a first real run, and batching 50-100 runs. Real-backend dependencies are in [requirements-annotation.txt](requirements-annotation.txt) (mock mode needs no installs). Full environment/library reference: [docs/environment-and-libraries.md](docs/environment-and-libraries.md).

## Current Direction: Annotation-Only QA Generation

QA pairs are currently generated from **real JIGSAWS annotations only** (skill level, GRS subscores, gesture spans) — no frames are extracted or sent to the model. The go-forward runner is [scripts/run_annotation_qa_jigsaws.py](scripts/run_annotation_qa_jigsaws.py) with the annotation-only prompts ([system-prompt-A-D.md](Prompts_And_Pipeline/system-prompt-A-D.md), the canonical [template-d-question-templates.md](Prompts_And_Pipeline/template-d-question-templates.md), and [system-prompt-jigsaws-v1.1.md](Prompts_And_Pipeline/system-prompt-jigsaws-v1.1.md)). All answers come from the model; label-derived text is stored only in clearly named reference/metadata fields, never as the QA answer. Every record is stamped with `model.backend` (`real` or `mock`), and mock records are watermarked and kept out of the main output JSONL.

Deprecated (frame-based / superseded):

- `first_run/scripts/run_jigsaws_template_d_qwen.py` — wrote label-templated canned text into the `answer` field; do not use its outputs as generated QA data. The previously tracked sample `first_run/results/jigsaws-first-run-qwen.jsonl` was removed for that reason.
- `first_run/scripts/run_jigsaws_d1_qwen36_batch.py` — frame-based; retained for a later visual-grounding phase, now with mock/real backend stamping.

## Local Run

The first runnable JIGSAWS Template D workflow lives in [`first_run/`](first_run/). Fresh local or cluster runs should write generated JSONL, frames, logs, and smoke-test artifacts under ignored `outputs/` paths.

Quick syntax and dry-run checks once the JIGSAWS Suturing dataset path is available:

```bash
python3 -m compileall first_run/scripts
python3 first_run/scripts/run_jigsaws_template_d_qwen.py \
  --dataset-root /path/to/JIGSAWS/Suturing \
  --dry-run \
  --output-jsonl outputs/jigsaws-first-run-dry-run.jsonl
```

The full model run requires the JIGSAWS Suturing data, `ffmpeg`/`ffprobe`, a CUDA-capable runtime, and Hugging Face access for the configured Qwen model.

## Prompt and QA Pipeline

The prompt stack is designed for structured surgical coaching QA from intraoperative video, frame samples, annotations, and dataset labels. Steps 1-3 define the task and generate candidate QA pairs; the remaining stages validate format, correctness, grounding, and human-review readiness.

### Prompt Assets

- [System prompt](Prompts_And_Pipeline/system-prompt-jigsaws-v1.1.md): instructs the model to act as a surgical evaluator for JIGSAWS Suturing and return only a compact three-field JSON answer with explicit field routing.
- [Current prompt Python module](first_run/scripts/current_system_prompt.py): loads the latest system prompt Markdown and renders Template D questions from the canonical template file.
- [LLM rubric](Prompts_And_Pipeline/llm-rubric-v1.1.md): scores the generated answer against verified ground truth for label accuracy, rationale quality, and evidence precision.
- [VLM rubric](Prompts_And_Pipeline/vlm-rubric-v1.0.md): checks whether the proposed QA pair is visually supported by the relevant video frame, clip, or timestamp.
- [Pipeline notes](Prompts_And_Pipeline/pipeline-notes.md): working notes for the generation and checkpoint flow summarized below.

### 1. Define the Taxonomy

Start with the dataset-specific label space and expected answer types. For JIGSAWS Suturing, this can include anatomy labels, instrument labels, action labels, error types, next operative step, coaching-feedback category, complication or adverse-event type, and question type.

### 2. Set the System Prompt and Output Schema

Constrain the model to the current dataset, task instructions, annotation rules, and scoring rubric. Each generated answer should use the same JSON shape:

```json
{
  "question": "",
  "answer": "",
  "rationale": ""
}
```

The `answer` field should be an exact taxonomy label or concise coaching response when the task requires one. The `rationale` should be a single clinically grounded justification that includes the relevant timestamp or frame range, plus any supporting details such as visible evidence or improvement target.

Canonical D1-D5 question wording lives in [Template D question templates](Prompts_And_Pipeline/template-d-question-templates.md). Copy the selected template exactly and replace `[a certain video span]` with the target video span when generating an item.

Field routing:

- `question`: the requested question only.
- `answer`: trainee-facing coaching only. For coaching tasks, use 2-4 short, instructive sentences with concrete movement instructions. Do not hide behind phrases like "plan the next movement" unless the plan is spelled out.
- `rationale`: frame range, visible evidence, annotation support, and any uncertainty or limitation.

### 3. Generate Candidate QA Pairs

Use dataset labels, captions, timestamps, and annotations to generate candidate QA pairs with a generation model such as Qwen. Reference answers should be grounded in the available labels and temporal evidence, not inferred from generic surgical knowledge alone.

### 4. Run Format and Taxonomy Checks

Parse every generated answer before scoring it. Keep examples that follow the required JSON schema and match the expected taxonomy; drop examples with malformed JSON, missing fields, invalid labels, or unsupported answers.

### 5. Score with an LLM Evaluator

Use a separate evaluator model to score each valid answer against verified ground truth:

| Component | 2 | 1 | 0 |
| --- | --- | --- | --- |
| Label / next step | Exact and specific taxonomy match | Partially correct or too broad | Incorrect, unsafe, or outside the taxonomy |
| Rationale | Clinically correct and grounded in the evidence | Plausible but incomplete or generic | Wrong, hallucinated, or unsupported |
| Evidence timestamp | Exact timestamp or frame range supports the answer | Nearby or only partially supportive | Missing, wrong, or unrelated |

The structured QA score is the sum of the three component scores, with a maximum score of 6.

### 6. Verify Visual Grounding with a VLM

Run a VLM checkpoint on the video frame, clip, or timestamp with the generated QA JSON. This step verifies that the answer matches the visual evidence: correct surgery type and anatomy, correct timestamp, and visible action or tool-tissue interaction.

### 7. Human Verification

Manually review flagged cases and audit a random sample before scaling. For the first small experiment, review about 100 QA pairs, remove invalid samples, and use the audit results to decide whether the automatic checks are reliable enough for larger runs.

### Recommended Metrics

- Answer format validity rate
- Label accuracy or taxonomy-match rate
- Average rationale score
- Average evidence-grounding score
- Average VLM visual-grounding score
- Total structured-answer score
