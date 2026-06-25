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

- `D1` one-sentence attending feedback.
- `D3` immediate corrective action.
- `D4` practice recommendation.
- `D5` positive reinforcement.

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

Rules:
1. Do not invent anatomy, bleeding, complications, or patient-specific risk.
2. Do not call something an error unless the visual evidence or annotation supports it.
3. Use low GRS subscores as weakness signals, not as direct proof of what happened in one frame.
4. If visual evidence is missing, say the feedback is label-supported and requires visual confirmation.
5. Keep the coaching tone professional and concise.
6. Include source labels used and evidence status.
```

## Label-To-Feedback Mapping

Use this mapping to choose the feedback target:

| Low subscore | Coaching focus | Example feedback direction |
|---|---|---|
| Respect for tissue <= 2 | Tissue handling | Use gentler traction and avoid unnecessary force. |
| Suture/needle handling <= 2 | Needle control | Stabilize the needle angle before driving through the target. |
| Time and motion <= 2 | Economy of motion | Reduce extra instrument travel and plan the next movement before advancing. |
| Flow of operation <= 2 | Procedural flow | Pause, reorient, and complete one step cleanly before repositioning. |
| Overall performance <= 2 | General technique | Focus on controlled bimanual movements and consistent needle handling. |
| Final product quality <= 2 | Output quality | Practice consistent spacing, depth, and tension across the stitch. |

High subscore guidance:

| High subscore | Positive reinforcement focus |
|---|---|
| Respect for tissue >= 4 | Controlled tissue interaction |
| Suture/needle handling >= 4 | Stable needle angle and controlled driving |
| Time and motion >= 4 | Efficient movement |
| Flow of operation >= 4 | Smooth task progression |
| Final product quality >= 4 | Consistent final stitch quality |

## Output Structure

Each generated item should follow this shape:

```json
{
  "qa_id": "jigsaws_suturing_d_<trial>_<template>_<frame_range>",
  "dataset": "JIGSAWS",
  "procedure_or_task": "Suturing",
  "video_id": "<trial_id>_<capture_id>",
  "clip_id": "<trial_id>_<start_frame>_<end_frame>",
  "frame_id": null,
  "timestamp_or_frame": "<start_frame>-<end_frame>",
  "template_category": "D",
  "template_id": "D1",
  "question_type": "coaching_feedback",
  "question": "...",
  "answer": "...",
  "rationale": "...",
  "visible_evidence": "...",
  "source_labels_used": ["gesture_id", "gesture_frame_range", "skill_level", "grs_subscores"],
  "skill_domain": ["needle handling", "economy of motion"],
  "learner_level": "resident",
  "confidence": "medium",
  "evidence_status": "partially_supported",
  "requires_expert_review": true,
  "coaching": {
    "feedback_type": "one_sentence",
    "feedback_points": [
      {
        "priority": "technique",
        "message": "..."
      }
    ],
    "immediate_action": "...",
    "practice_drill": null,
    "tone": "supportive"
  },
  "source_annotation": {
    "trial_id": "...",
    "gesture_id": "...",
    "start_frame": 0,
    "end_frame": 0,
    "skill_level": "N",
    "grs_total": 0,
    "grs_subscores": {
      "respect_for_tissue": 0,
      "suture_needle_handling": 0,
      "time_and_motion": 0,
      "flow_of_operation": 0,
      "overall_performance": 0,
      "quality_of_final_product": 0
    }
  }
}
```

## Structured Free-Response Alternatives

For the first run, use one of two middle-ground formats instead of fully open-ended answers.

`component_structured` asks for:

```json
{
  "best_next_step": "...",
  "one_sentence_rationale": "...",
  "supporting_evidence_span": {
    "type": "frame_range",
    "start_frame": 371,
    "end_frame": 590,
    "gesture_id": "G8",
    "evidence_source": ["gesture_transcription", "sampled_frames", "GRS_subscores"],
    "evidence_status": "partially_supported"
  },
  "uncertainty": "..."
}
```

Score each component separately from 0-2:

- `best_next_step`: correctness and actionability.
- `one_sentence_rationale`: concise grounding in labels/evidence.
- `supporting_evidence_span`: correct timestamp/frame span or explicit trial-level evidence statement.

`taxonomy_structured` asks for:

```json
{
  "taxonomy_version": "jigsaws_template_d_v0.1",
  "taxonomy_labels": {
    "anatomy": "suturing_pad_or_simulated_tissue",
    "error_type": "inefficient_motion_label_supported",
    "next_operative_step": "reduce_extra_motion",
    "complication": "none_visible_or_not_annotated",
    "coaching_feedback_category": "economy_of_motion"
  },
  "short_explanation": "...",
  "supporting_evidence_span": {
    "type": "frame_range",
    "start_frame": 371,
    "end_frame": 590,
    "gesture_id": "G8",
    "evidence_source": ["gesture_transcription", "sampled_frames", "GRS_subscores"],
    "evidence_status": "partially_supported"
  },
  "uncertainty": "..."
}
```

Score `taxonomy_label`, `rationale`, and `evidence_span` separately from 0-2. This format is useful when we want easier standardization across anatomy, error type, next operative step, complication, and coaching feedback category.

## Seed Cases

Use these as first-run examples:

- `Suturing_B001`: novice, GRS total 13, subscores `3,2,1,2,2,3`. Best for D1, D3, D4 around economy of motion, needle handling, and flow.
- `Suturing_C004`: intermediate, GRS total 30, all subscores 5. Best for D5 positive reinforcement and contrastive examples.

## First-Run Policy

For tomorrow's first run, keep the answer space structured:

- one short coaching sentence for `D1`;
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
