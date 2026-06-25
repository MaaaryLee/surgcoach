# JIGSAWS Template D First Run

This folder contains the first end-to-end Template D coaching-feedback run.

## Reproduce On OPrime

Install the model environment:

```bash
./first_run/scripts/setup_oprime_qwen25vl.sh
```

Run the same JIGSAWS example:

```bash
PYTHONPATH=/mnt/sun/shared/datasets/surgical_skill/.python/qwen25vl/site-packages \
HF_HOME=/mnt/sun/shared/datasets/surgical_skill/.cache/huggingface/mairuili \
CUDA_VISIBLE_DEVICES=0 \
python3 first_run/scripts/run_jigsaws_template_d_qwen.py \
  --trial-id Suturing_B001 \
  --template-id D1 \
  --frame-range 371-590 \
  --output-jsonl first_run/results/jigsaws-first-run-qwen.jsonl \
  --frame-output-dir first_run/results/frames \
  --frame-sampler ffmpeg \
  --num-frames 4 \
  --max-new-tokens 256 \
  --device-map cuda:0
```

## Sampling Strategy

The run uses deterministic gesture-span sampling. For frame span `371-590`, four frames are sampled evenly:

```text
371, 444, 517, 590
```

## Response Formats

Two structured free-response formats are included:

- `component_structured`: best next step, one-sentence rationale, evidence span, uncertainty.
- `taxonomy_structured`: taxonomy labels, short explanation, evidence span, uncertainty.

Each format has a 0-2 component rubric in `docs/jigsaws-template-d-structured-response-training.md`.
