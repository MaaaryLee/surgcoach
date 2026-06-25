# SurgCoach

First-run materials for structured coaching-feedback QA on surgical skill videos.

This repository currently contains a focused JIGSAWS Suturing smoke test using Template D coaching questions and `Qwen/Qwen2.5-VL-7B-Instruct`.

## Contents

- `first_run/scripts/`: OPrime setup and JIGSAWS Template D runner.
- `first_run/docs/`: dataset-selection notes, prompt format, and structured response rubrics.
- `first_run/data/`: seed QA pairs and structured free-response examples.
- `first_run/results/`: the first Qwen output and sampled input frames.

## First Run Summary

- Dataset: JIGSAWS Suturing
- Trial: `Suturing_B001`
- Video: `Suturing_B001_capture1`
- Frame span: `371-590`
- Gesture: `G8`
- Template: `D1`, one-sentence attending feedback
- Model: `Qwen/Qwen2.5-VL-7B-Instruct`

The label-supported target was economy of motion because the weakest GRS subscore was `time_and_motion = 1`.

Expected coaching target:

```text
Reduce extra instrument travel and plan the next movement before advancing.
```

Model coaching output:

```text
Ensure smooth and efficient suturing by minimizing unnecessary movements and planning ahead.
```

The model also reported that the sampled frames did not fully confirm the label-supported weakness. This makes the run useful as a first evidence-grounding check rather than a final evaluation result.
