# JIGSAWS Suturing Template D Starter

Purpose: generate coaching-feedback QA pairs for a first JIGSAWS-Suturing run. This starter is intentionally conservative: it uses JIGSAWS labels to constrain the feedback, and it leaves visual claims to the model or human reviewer.

## Dataset Choice

Use `JIGSAWS/Suturing` first.

Why this is feasible:

- It has a small, controlled scope: suturing simulation rather than full intraoperative anatomy.
- It has performance labels: self-proclaimed skill level (`N`, `I`, `E`), modified GRS total score, and six GRS subscore fields.
- It has temporal grounding: each trial has gesture transcription files with `start_frame end_frame gesture_id`.
- It has synchronized source media: two video captures per trial.

Observed OPrime layout:

```text
/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing/
  meta_file_Suturing.txt
  transcriptions/Suturing_*.txt
  video/Suturing_*_capture1.avi
  video/Suturing_*_capture2.avi
  kinematics/AllGestures/Suturing_*.txt
```

## Available Labels

`meta_file_Suturing.txt` columns:

```text
1: trial id
2: self-proclaimed skill level
   E = expert (>100 hours)
   I = intermediate (10-100 hours)
   N = novice (<10 hours)
3: modified GRS total score
4: Respect for tissue
5: Suture/needle handling
6: Time and motion
7: Flow of operation
8: Overall performance
9: Quality of final product
```

`transcriptions/Suturing_*.txt` columns:

```text
1: start frame
2: end frame
3: gesture id
```

## Template D Scope

Use these Template D variants first:

- `D1` One-Sentence Attending Feedback.
- `D2` Feedback With Priority.
- `D3` Corrective Action.
- `D4` Practice Recommendation.
- `D5` Positive Reinforcement.

Use `D2` cautiously. In JIGSAWS, "safety" should mean simulation/task safety, needle control, tissue handling, or instrument handling. Do not claim patient harm or intraoperative anatomy risk.

## Generation Prompt

```text
You are generating coaching-feedback QA pairs from JIGSAWS Suturing.

Input:
- trial_id: [trial_id]
- task: Suturing
- capture_id: [capture1|capture2]
- frame_range: [start_frame-end_frame]
- gesture_id: [G#]
- skill_level: [N|I|E]
- grs_total: [integer]
- grs_subscores:
  - respect_for_tissue: [1-5]
  - suture_needle_handling: [1-5]
  - time_and_motion: [1-5]
  - flow_of_operation: [1-5]
  - overall_performance: [1-5]
  - quality_of_final_product: [1-5]
- visual_evidence: [short model- or human-generated visual description]

Generate one Template D QA item. The answer must be specific, action-oriented, and grounded in the provided labels and visual evidence.

Canonical D1-D5 question wording lives in ../../Prompts_And_Pipeline/template-d-question-templates.md. Copy the selected template exactly and replace `[a certain video span]` with the target video span when generating an item.

Field routing:

- `question`: exact question text only.
- `answer`: detailed trainee-facing feedback only. Name the exact movement or habit the trainee should change, the technical field/domain when helpful, and the next concrete motion sequence.
- `rationale`: detailed analysis including frame range, visible evidence, annotation support, and any uncertainty or weak visual-evidence statement.

Rules:
1. Do not invent anatomy, bleeding, complications, or patient-specific risk.
2. Do not call something an error unless the visual evidence or annotation supports it.
3. Use low GRS subscores as weakness signals, not as direct proof of what happened in one frame.
4. If visual evidence is missing, say the feedback is label-supported and requires visual confirmation.
5. Keep the coaching tone professional and concise, but do not force D1 feedback into a single sentence.
6. Do not add metadata fields. If source labels, evidence status, technical domain, or uncertainty matter, write them in prose inside `answer` or `rationale`.
7. Avoid vague phrases such as "plan the next movement" unless the output states the actual plan: choose the next bite point, set the needle angle, align the instruments, drive or regrasp, then continue.
```

## Label-To-Feedback Mapping

Use this mapping to choose the feedback target:

| Low subscore | Coaching focus | Example feedback direction |
|---|---|---|
| Respect for tissue <= 2 | Tissue handling | Lighten traction on the simulated tissue, use the assisting instrument only to expose the bite, and stop pulling once the needle path is visible. |
| Suture/needle handling <= 2 | Needle control | Set the needle angle for the intended bite, stabilize the needle with the driver before entry, and drive with a controlled wrist rotation instead of pushing or dragging. |
| Time and motion <= 2 | Economy of motion | Before advancing, choose the next bite point, set the needle angle, drive through in one controlled arc, and avoid extra instrument travel between those steps. |
| Flow of operation <= 2 | Procedural flow | Stop the extra repositioning, identify the next bite point, align both instruments, and complete that single pass before changing tasks. |
| Overall performance <= 2 | General technique | Slow the sequence down, keep both instruments coordinated at the needle, and complete one clean grasp-drive-release cycle before adjusting. |
| Final product quality <= 2 | Output quality | Aim for equal bite spacing and depth, remove only the slack needed, and check tension before placing the next bite. |

High subscore guidance:

| High subscore | Positive reinforcement focus |
|---|---|
| Respect for tissue >= 4 | Controlled tissue interaction |
| Suture/needle handling >= 4 | Stable needle angle and controlled driving |
| Time and motion >= 4 | Efficient movement |
| Flow of operation >= 4 | Smooth task progression |
| Final product quality >= 4 | Consistent final stitch quality |

## Output Structure

Each generated QA item should follow this shape:

```json
{
  "question": "...",
  "answer": "...",
  "rationale": "..."
}
```

## Output Policy

For the first run, use only the three-field free-response QA format:

```json
{
  "question": "...",
  "answer": "...",
  "rationale": "..."
}
```

Do not use component-structured or taxonomy-structured response formats for generated QA. If a technical category, evidence span, uncertainty statement, or annotation detail matters, write it in natural language inside `answer` or `rationale`.

## Seed Cases

Use these as first-run examples:

- `Suturing_B001`: novice, GRS total 13, subscores `3,2,1,2,2,3`. Best for D1, D3, D4 around economy of motion, needle handling, and flow.
- `Suturing_C004`: intermediate, GRS total 30, all subscores 5. Best for D5 positive reinforcement and contrastive examples.

## First-Run Policy

For the first run, keep the answer space detailed but schema-simple:

- a detailed instructional coaching note for `D1`;
- one immediate action for `D3`;
- one drill name plus one reason for `D4`;
- one positive behavior plus one reason for `D5`;
- no open-ended medical advice.

Recommended first batch:

```text
10 D1 items
10 D3 items
5 D4 items
5 D5 items
```

This is enough to test the model/prompt pipeline without overcommitting to noisy open-ended evaluation.
