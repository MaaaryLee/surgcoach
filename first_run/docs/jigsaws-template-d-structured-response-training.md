# Template D Structured Free-Response Training

Purpose: define two middle-ground answer formats for Template D coaching QA. Both avoid fully open-ended answers while still letting the model produce short natural-language reasoning.

Use these formats for JIGSAWS Suturing first runs, especially when the available labels are trial-level GRS scores plus gesture frame spans rather than frame-level error annotations.

## Format A: Component-Structured Free Response

Training instruction:

```text
You are answering a surgical coaching QA item from JIGSAWS Suturing.

Return strict JSON with exactly these keys:
- best_next_step: one concrete action the trainee should take next
- one_sentence_rationale: exactly one sentence tying the step to the labels and/or visible evidence
- supporting_evidence_span: object with type, start_frame, end_frame, gesture_id, evidence_source, evidence_status
- uncertainty: one short sentence

Rules:
1. Do not invent anatomy, bleeding, complications, or patient-specific risk.
2. If the visual evidence is ambiguous, say that the answer is label-supported but needs visual confirmation.
3. Keep the next step action-oriented and suitable for a trainee.
4. Keep the rationale to one sentence.
```

Example output:

```json
{
  "best_next_step": "Reduce extra instrument travel and plan the next movement before advancing.",
  "one_sentence_rationale": "The label-supported target is economy of motion because time_and_motion is the weakest GRS subscore (1/5), with frames 371-590 used as the visual evidence span.",
  "supporting_evidence_span": {
    "type": "frame_range",
    "start_frame": 371,
    "end_frame": 590,
    "gesture_id": "G8",
    "evidence_source": ["gesture_transcription", "sampled_frames", "GRS_subscores"],
    "evidence_status": "partially_supported"
  },
  "uncertainty": "The GRS subscore is trial-level, so the frame span should be used to verify whether the target is visibly supported."
}
```

0-2 scoring:

| Component | 0 | 1 | 2 |
|---|---|---|---|
| best_next_step | Unsafe, irrelevant, or unsupported | Reasonable but generic | Specific, action-oriented, and aligned with labels/evidence |
| one_sentence_rationale | Missing, contradictory, or not one sentence | One sentence but generic | One sentence tied to GRS label and/or visual evidence |
| supporting_evidence_span | Missing | Broad/incomplete | Correct frame range or clear trial-level evidence statement |

## Format B: Taxonomy-Structured Free Response

Training instruction:

```text
You are answering a surgical coaching QA item from JIGSAWS Suturing.

First choose labels from the fixed taxonomy. Then provide one short explanation.

Return strict JSON with exactly these keys:
- taxonomy_version
- taxonomy_labels: object with anatomy, error_type, next_operative_step, complication, coaching_feedback_category
- short_explanation: exactly one sentence
- supporting_evidence_span: object with type, start_frame, end_frame, gesture_id, evidence_source, evidence_status
- uncertainty: one short sentence

Allowed coaching_feedback_category values:
- tissue_handling
- needle_control
- economy_of_motion
- procedural_flow
- overall_technique
- final_product_quality
- positive_reinforcement_<subscore_name>
- insufficient_visual_evidence

Allowed complication values:
- none_visible_or_not_annotated
- not_assessable_from_clip

Rules:
1. Use the taxonomy label that best matches the annotation-supported target.
2. Do not claim a complication unless it is visible or annotated.
3. If the frame evidence is weak, keep the taxonomy label but state uncertainty.
```

Example output:

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
  "short_explanation": "The label-supported target is economy of motion because time_and_motion is the weakest GRS subscore (1/5), with frames 371-590 used as the visual evidence span.",
  "supporting_evidence_span": {
    "type": "frame_range",
    "start_frame": 371,
    "end_frame": 590,
    "gesture_id": "G8",
    "evidence_source": ["gesture_transcription", "sampled_frames", "GRS_subscores"],
    "evidence_status": "partially_supported"
  },
  "uncertainty": "The taxonomy label is label-supported; visual confirmation is still needed when the frame evidence is ambiguous."
}
```

0-2 scoring:

| Component | 0 | 1 | 2 |
|---|---|---|---|
| taxonomy_label | Incorrect or unsupported | Broad category partially correct | Correct label aligned with annotation/evidence |
| rationale | Missing or contradictory | Generic/incomplete | Concise and grounded |
| evidence_span | Missing | Broad/incomplete | Correct frame range or clear trial-level evidence statement |

## Recommendation For First Run

Use Format A first for model behavior testing because it is easier to read and score manually. Use Format B in parallel on the same clips to test whether taxonomy labels make evaluation more standardized.
