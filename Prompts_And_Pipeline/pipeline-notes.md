# Prompt and QA Pipeline Notes

Steps 1-3 generate candidate QA pairs. Steps 4-7 validate format, correctness, visual grounding, and human-review readiness.

## 1. Define the Taxonomy

Define the dataset-specific label space before generation. Candidate labels can include:

- Anatomy label
- Instrument label
- Action label
- Error type
- Next operative step
- Coaching feedback category
- Complication or adverse event type
- Question type

## 2. Set the System Prompt and Output Schema

Define the input annotations, task instructions, and output format. Candidate QA pairs should use a structured answer schema:

```json
{
  "question": "",
  "answer": "",
  "rationale": ""
}
```

Put supporting details such as visible evidence, one improvement, and evidence timestamp/frame range inside `rationale` instead of adding extra top-level fields.

Canonical D1-D5 question wording lives in [Template D question templates](template-questions-A-D.md). Copy the selected template exactly and replace `[a certain video span]` with the target video span when generating an item.

Route content by field:

- `question`: exact question text.
- `answer`: trainee-facing feedback only. Use 2-4 short, specific, instructive sentences that name the movement problem, the next movement, and how to perform it.
- `rationale`: exact frame or frame range, visible evidence, annotation support, and any uncertainty or weak visual-grounding note.

Avoid vague feedback such as "plan the next movement" unless the output explains the actual plan, for example choosing the next bite point, setting the needle angle, aligning instruments, driving or regrasping, and then continuing.

## 3. Generate Candidate QA Pairs

Use an LLM, such as Qwen, to generate QA pairs from dataset labels, captions, timestamps, and annotations. Reference answers should be grounded in the source annotation data.

Current phase is **annotation-only**: no frames are extracted or sent to the model, and answers must never claim visual observations. The model generates the answer itself from the annotations; label-derived reference text may only appear in clearly named metadata fields (for example `label_supported_reference_feedback`), never in the `answer` field. Every generated record must carry a `backend` stamp (`real` or `mock`); mock records are watermarked and stored separately from real output.

## 4. Check Format and Taxonomy Validity

Before scoring, verify that each generated answer follows the required schema. Use exact-match or taxonomy-match checks for controlled labels.

Drop examples with malformed JSON, missing fields, invalid labels, or unsupported answers.

## 5. Score Each Component with an LLM Evaluator

Use a separate evaluator model and a 0-2 rubric for each answer component.

| Component | 2 | 1 | 0 |
| --- | --- | --- | --- |
| Controlled label / next step | Correct, concrete, and instructive | Partially correct or too broad | Incorrect or unsafe |
| Rationale | Clinically correct and grounded in video or annotations | Plausible but incomplete or generic | Wrong, hallucinated, or unsupported |
| Evidence span | Correct timestamp supporting the answer | Nearby or partially supportive | Missing or wrong |

Structured QA score = label score + rationale score + evidence score.

Maximum score: 6.

Recommended aggregate metrics:

- Label accuracy
- Rationale score
- Evidence grounding score
- Total structured-answer score
- Answer format validity rate

## 6. Run VLM Visual-Evidence Check

Give the VLM the video or frame, the question, and the required JSON format. This checkpoint verifies that the QA pair is visually grounded, timestamp-specific, and matched to the correct surgery.

Deferred during the annotation-only phase: this check applies once frames are reintroduced for verification. It must not be used to add visual claims to annotation-only QA pairs.

Use the VLM rubric for this step.

## 7. Run Human Verification

Review flagged cases and audit a random sample before scaling. For a first small experiment, manually review about 100 QA pairs and remove invalid samples.

Human verification is important at this stage. After small-scale manual validation shows that the automatic checks are effective, the same checks can be used more heavily for larger sample generation.
