# JIGSAWS Template D First Run

This folder contains the first end-to-end Template D coaching-feedback run.

## First-Run Setup

Run these commands from the `surgcoach` directory:

```bash
cd /path/to/surgcoach
```

The first run uses:

- Dataset: `/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing`
- Trial: `Suturing_B001`
- Capture: `capture1`
- Template: `D1` coaching feedback
- Frame range: `371-590`
- Model: `Qwen/Qwen2.5-VL-7B-Instruct`
- Tracked sample JSONL: `first_run/results/jigsaws-first-run-qwen.jsonl`
- Tracked sample frames: `first_run/results/frames/`

Use ignored `outputs/` paths for fresh local or cluster runs so reproducing the pipeline does not dirty the repository.

The setup script installs the Qwen2.5-VL runtime into shared dataset storage so the repo does not need to track large dependencies:

```text
ENV_DIR=/mnt/sun/shared/datasets/surgical_skill/.envs/qwen25vl
PACKAGE_DIR=/mnt/sun/shared/datasets/surgical_skill/.python/qwen25vl/site-packages
TMPDIR=/mnt/sun/shared/datasets/surgical_skill/.tmp/qwen25vl
PIP_CACHE_DIR=/mnt/sun/shared/datasets/surgical_skill/.cache/pip/mairuili-qwen25vl
HF_HOME=/mnt/sun/shared/datasets/surgical_skill/.cache/huggingface/mairuili
```

Prerequisites on the run host:

- Python 3 with `venv`, or permission to install packages into `PACKAGE_DIR`.
- CUDA 12.4-compatible GPU runtime for `torch==2.6.0+cu124`.
- `ffmpeg` and `ffprobe` on `PATH` when using `--frame-sampler ffmpeg`.
- Read access to the JIGSAWS Suturing metadata, transcriptions, and video files.
- Hugging Face access for `Qwen/Qwen2.5-VL-7B-Instruct`.

## Reproduce On OPrime

Install the model environment:

```bash
./first_run/scripts/setup_oprime_qwen25vl.sh
```

This downloads the model into `HF_HOME`, installs the packages in `requirements-qwen25vl.txt`, and prints a quick import/CUDA check for `torch`, `transformers`, `qwen_vl_utils`, and `decord`.

Before loading the model, you can verify that metadata parsing, prompt construction, and JSONL writing work:

```bash
DATASET_ROOT=/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing
python3 first_run/scripts/run_jigsaws_template_d_qwen.py \
  --dataset-root "$DATASET_ROOT" \
  --trial-id Suturing_B001 \
  --template-id D1 \
  --frame-range 371-590 \
  --output-jsonl outputs/jigsaws-first-run-dry-run.jsonl \
  --dry-run
```

Run the same JIGSAWS example:

```bash
DATASET_ROOT=/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing
PYTHONPATH=/mnt/sun/shared/datasets/surgical_skill/.python/qwen25vl/site-packages \
HF_HOME=/mnt/sun/shared/datasets/surgical_skill/.cache/huggingface/mairuili \
CUDA_VISIBLE_DEVICES=0 \
python3 first_run/scripts/run_jigsaws_template_d_qwen.py \
  --dataset-root "$DATASET_ROOT" \
  --trial-id Suturing_B001 \
  --template-id D1 \
  --frame-range 371-590 \
  --output-jsonl outputs/jigsaws-first-run-qwen.jsonl \
  --frame-output-dir outputs/jigsaws-suturing-template-d-frames \
  --frame-sampler ffmpeg \
  --num-frames 4 \
  --max-new-tokens 256 \
  --device-map cuda:0
```

The command appends one record to the output JSONL. Each record includes the generated question, label-supported expected answer, source labels, scoring rubric, sampled frame paths, model output, and the full prompt sent to Qwen.

## Batch Generation and Evaluator Smoke

The Qwen3.6 D1 batch runner can generate full-shard QA examples and can also dry-run candidate construction without loading a model:

```bash
DATASET_ROOT=/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing
python3 first_run/scripts/run_jigsaws_d1_qwen36_batch.py \
  --dataset-root "$DATASET_ROOT" \
  --output-jsonl outputs/new_pipeline/generated_qa_shard0.jsonl \
  --rejected-jsonl outputs/new_pipeline/rejected_qa_shard0.jsonl \
  --frame-output-dir outputs/new_pipeline/frames_shard0 \
  --num-shards 2 \
  --shard-index 0 \
  --max-examples 2 \
  --dry-run
```

SLURM wrappers are included for OPrime and Longleaf. They infer the script directory from their own location, so they can be submitted from the repo clone while writing generated artifacts under ignored `outputs/` paths.

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
