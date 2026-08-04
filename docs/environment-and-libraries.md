# Environment And Library Documentation

Complete reference for every environment this project runs in, the libraries each pipeline needs, and how to set them up. Covers the current **annotation-only QA generation** phase and the legacy/deferred frame-based pipelines.

Last verified: 2026-07-20.

## 1. Repositories

| Repo | Role |
| --- | --- |
| `~/surgcoach` | **The single team repo** (github.com/MaaaryLee/surgcoach): templates A-D prompts (`Prompts_And_Pipeline/system-prompt-A-D.md`, `Prompts_And_Pipeline/template-questions-A-D.md`), the current annotation-only runner (`scripts/run_annotation_qa_jigsaws.py`), rubrics, pipeline notes, docs, and the legacy frame-based runners under `first_run/scripts/`. |
| `~/surgical-error-detection` | Local-only archive: A-J prompt research library, schemas, mindmaps, and the legacy cluster-run record. Not needed for generation. |

Copies on the compute clusters are synced manually (see section 3), and results are copied back into `outputs/` locally.

## 2. Local Machine (macOS)

- macOS 15.7 (Darwin 24.6), Apple Silicon, no CUDA.
- System Python 3.9.6 — sufficient for everything that runs locally.

What runs locally (no ML dependencies needed, though the event localizer needs `numpy` — it reads the kinematics as arrays, with or without a model):

- `scripts/run_annotation_qa_jigsaws.py --backend mock` — full pipeline smoke test (annotation parsing, template gating, exact D-wording validation, record schema). `torch`/`transformers` are imported only inside the real backend, so mock mode runs on stock Python.
- `python3 -m compileall` syntax checks, prompt/template editing, JSONL validation.
- Fixture data for local smokes: `outputs/local_smoke_fixture/Suturing/` (fabricated trial `Suturing_Z999`, 0-byte videos — smoke tests only, never data).

Local smoke example:

```bash
python3 scripts/run_annotation_qa_jigsaws.py \
  --dataset-root outputs/local_smoke_fixture/Suturing \
  --system-prompt Prompts_And_Pipeline/system-prompt-A-D.md \
  --trial-id Suturing_Z999 \
  --backend mock \
  --output-dir /tmp/anno_smoke
```

Real inference does NOT run locally (no CUDA); use a cluster.

## 3. Compute Clusters (Slurm)

Real inference runs on a GPU cluster through Slurm. Two setup patterns are in use. Concrete hostnames, storage mounts, usernames, and Slurm account names are intentionally **not** recorded in this doc — they live in the sbatch scripts you actually submit and in your own cluster login notes. Always pass the Slurm account for **this** project at submit time where your cluster requires one (`sbatch --account=<your-project-account> ...`); no account is hardcoded in the scripts.

### 3.1 Shared-mount pattern (primary cluster)

- Typical generation job: `--gres=gpu:4 --cpus-per-task=8 --mem=160G` (evaluator smoke: 1 GPU).
- A shared storage root provides: the datasets (JIGSAWS/Suturing, plus PitVQA, CoPESD, MultiBypass140, Endoscapes2023), prebuilt Python package directories injected via `PYTHONPATH` (compute nodes don't use venvs), a shared HF cache (`HF_HOME`), and scratch space (`TMPDIR`). Some source builds also need `CPATH` pointed at extracted Python headers — see the existing sbatch scripts for the working pattern.
- Standard env block shape used by every sbatch on this cluster:

```bash
export PYTHONPATH="<shared prebuilt site-packages>${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="<shared HF cache dir>"
export TMPDIR="<shared scratch>/<job-name>"
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"
export PYTHONFAULTHANDLER=1
```

- Environment bootstrap: `first_run/scripts/setup_oprime_qwen25vl.sh` (tries a venv; falls back to `pip install --target` into the shared site-packages dir, which is the mode the sbatch scripts assume). It also pre-downloads the model snapshot via `huggingface_hub.snapshot_download`.
- Annotation-only job template: `scripts/run_annotation_qa_jigsaws_oprime.sbatch` — set `WORK_DIR`, `DATASET_ROOT`, and the log/output paths for your cluster before submitting. It expects `system-prompt-A-D.md` + `template-questions-A-D.md` copied into `WORK_DIR` and the runner under `WORK_DIR/scripts/`.

### 3.2 Module + venv pattern (secondary cluster)

- GPU jobs typically `--gres=gpu:2 --mem=128G` on the GPU partition; CPU-only setup jobs on the general partition.
- Uses environment modules + a per-repo venv (no shared site-packages):

```bash
module load python/3.12.4 cuda/12.4
python3 -m venv .venv-qwen36 && source .venv-qwen36/bin/activate
```

- Bootstrap: `first_run/scripts/setup_longleaf_qwen36.sbatch` — installs pinned torch/cu124 wheels, then `transformers` **from git** (`pip install --no-deps git+https://github.com/huggingface/transformers.git`) because Qwen3.6 requires a very recent transformers; plus `regex sentencepiece "tokenizers>=0.22,<0.23.1" protobuf`.
- `HF_HOME`, `TMPDIR`, and caches live under the job's work dir; the dataset is copied to `$WORK_DIR/data/JIGSAWS/Suturing`.

## 4. Datasets And Annotations

The annotation-only pipeline reads three annotation sources per JIGSAWS task, all plain text. The first two serve templates A-D; the third is read only by the event-grounded pipeline:

| File | Content | Used for |
| --- | --- | --- |
| `<root>/meta_file_Suturing.txt` | One line per trial: `trial_id skill_level grs_total` + 6 GRS subscores (`respect_for_tissue`, `suture_needle_handling`, `time_and_motion`, `flow_of_operation`, `overall_performance`, `quality_of_final_product`), each 1-5 | C1-C3, C6, C7 scores; D coaching focus |
| `<root>/transcriptions/<trial_id>.txt` | One line per gesture: `start_frame end_frame gesture_id` (G1-G15) | A3 action recognition; per-span QA items; event span boundaries |
| `<root>/kinematics/<sub>/<trial_id>.txt` | 76 variables per frame, on the transcription's frame numbering. Slave columns are the patient-side instruments | `localize_events.py` path length, idle fraction, jerk proxy, regrasp counts |

A root can have the first two and not the third, which is easy to miss: `kinematics/` was never exercised by the A-D jobs, and when it is absent the event detectors return zero events with no error at all. Both event sbatch scripts therefore count the files and refuse to start rather than producing an empty batch that looks like a clean run. `scripts/preflight_event_qa.sbatch` reports the count per task.

`<root>/video/*.avi` exists but is **not read** in the current phase.

Template support on JIGSAWS annotations (enforced by the runner — unsupported requests are refused with a reason, never generated):

- Supported: A3, C1, C2, C3, C6, C7, D1-D5
- Unsupported: A1, A2, A4 (no anatomy/instrument/visual-quality labels), B1-B5 (no safety labels), C4, C5 (no bimanual/targeting labels)

## 5. Python Libraries

### 5.1 Annotation-only pipeline (current) — minimal set

Real backend on a cluster:

| Package | Version | Why |
| --- | --- | --- |
| `torch` | `2.6.0+cu124` | inference |
| `transformers` | latest (git main on the venv-based cluster; shared prebuilt package dir on the shared-mount cluster) | Qwen3.6 architecture support; `>=4.51,<5` is enough for Qwen2.5-VL only |
| `accelerate` | `>=0.26,<2` | `device_map="auto"` sharding |
| `huggingface_hub` | `>=0.30,<1` | model snapshots |
| `safetensors`, `numpy` | `>=0.4` / `>=1.24,<3` | weights, arrays |
| `sentencepiece`, `tokenizers>=0.22,<0.23.1`, `regex`, `protobuf` | (venv-cluster pins) | tokenizer stack for the git transformers build |

Mock backend (local): **none** — standard library only.

Explicitly NOT needed in this phase: `decord`, `pillow`, `torchvision`, `qwen-vl-utils` (those are frame-pipeline dependencies).

### 5.2 Legacy / deferred frame-based pipeline

Pinned in `first_run/requirements-qwen25vl.txt`:

```
accelerate>=0.26.0,<2.0.0
decord>=0.6.0                  # video frame extraction
huggingface_hub>=0.30.0,<1.0.0
numpy>=1.24.0,<3.0.0
pillow>=10.0.0                 # frame JPEG writing
qwen-vl-utils[decord]==0.0.8   # process_vision_info for Qwen VL chat template
safetensors>=0.4.0
torch==2.6.0+cu124
torchvision==0.21.0+cu124
transformers>=4.51.0,<5.0.0
```

Install with the PyTorch CUDA index: `pip install --extra-index-url https://download.pytorch.org/whl/cu124 -r requirements-qwen25vl.txt`.

System tools for the legacy pipeline: `ffmpeg`/`ffprobe` (video probing), CUDA 12.4 runtime.

## 6. Models

| Model | Role | Status |
| --- | --- | --- |
| [`Qwen/Qwen3.6-35B-A3B`](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) | QA generator (default in all current sbatch scripts) | **Verified public checkpoint (2026-07-20)**: multimodal MoE vision-language model, 35B total / 3B activated, 262k context, accepts text/image/video and works text-only for the annotation-only pipeline. Thinking mode is ON by default (`<think>...</think>` before the answer) — the runners' JSON parsers strip it. Requires the latest `transformers` (hence the git install in the secondary-cluster bootstrap). |
| `Qwen/Qwen2.5-VL-7B-Instruct` | Legacy frame-based generator (first run) | Loads with the `requirements-qwen25vl.txt` pins. |
| `google/gemma-4-31B-it` | LLM/VLM evaluator (rubric scoring, deferred visual check) | Used in evaluator smoke sbatch scripts; needs HF access approval for gated Gemma weights. |

All models load with `trust_remote_code=True`, bf16 on CUDA, greedy decoding (`do_sample=False`).

## 7. Environment Variables Reference

| Variable | Purpose | Primary cluster (shared-mount) | Secondary cluster (module+venv) |
| --- | --- | --- | --- |
| `PYTHONPATH` | shared prebuilt site-packages | shared package dirs (see sbatch) | (unused; venv) |
| `HF_HOME` | HF model/dataset cache | shared cache dir (see sbatch) | `$WORK_DIR/.cache/huggingface` |
| `TMPDIR` | scratch for pip/decode | shared scratch (see sbatch) | `$WORK_DIR/.tmp` |
| `PYTORCH_CUDA_ALLOC_CONF` | fragmentation guard | `expandable_segments:True` | same |
| `PYTHONFAULTHANDLER` | tracebacks on hangs/crashes | `1` | `1` |
| `CPATH` | Python headers for source builds | extracted headers dir (see sbatch) | (module provides) |
| `DATASET_ROOT`, `MODEL_ID`, `TRIAL_ID`, `TEMPLATES`, `OUTPUT_DIR`, `WORK_DIR` | per-job overrides | see each sbatch | see each sbatch |

## 8. Running The Annotation-Only Pipeline

Cluster (shared-mount pattern shown; adjust paths for your cluster):

```bash
# one-time per WORK_DIR: copy prompts + script
mkdir -p $WORK_DIR/scripts
cp Prompts_And_Pipeline/system-prompt-A-D.md Prompts_And_Pipeline/template-questions-A-D.md $WORK_DIR/
cp scripts/run_annotation_qa_jigsaws.py $WORK_DIR/scripts/

sbatch scripts/run_annotation_qa_jigsaws_oprime.sbatch          # defaults: Suturing_B001, all supported templates
TRIAL_ID=Suturing_C002 TEMPLATES=D1,D2 sbatch scripts/run_annotation_qa_jigsaws_oprime.sbatch
```

Outputs per run: `qa_records.jsonl` (real) or `qa_records.mock.jsonl` (mock) + `run_summary.json`.

### Provenance conventions (do not weaken these)

- Every record carries `model.backend` (`"real"`/`"mock"`), `model.real_model_inference`, the exact prompt, the raw model output, and the source annotation file paths/values.
- Mock output is watermarked `[MOCK OUTPUT - NOT MODEL-GENERATED - SMOKE TEST ONLY]`, gets `validation_status: "valid_mock"`, and is written to a separate `*.mock.jsonl` — mock text must never enter a real-data JSONL.
- The QA `answer` always comes from the model. Label-derived text may appear only in clearly named metadata fields (e.g. `label_supported_reference_feedback` in the legacy batch runner), never as the answer.
- D-template questions are validated against the canonical wording in `outputs/template-d-questions.md`; mismatches are `rejected`, not silently accepted.

## 9. Known Caveats

- `Qwen/Qwen3.6-35B-A3B` outputs thinking content by default; any new parsing code must strip `<think>...</think>` before JSON extraction (the existing runners already do).
- The legacy sample run `outputs/qwen36_jigsaws_real/` predates the 2026-07-02 prompt rewrite (10-field output vs. the current 3-field `question`/`answer`/`rationale` schema) — do not cite it as validating the current stack.
- `first_run/scripts/run_jigsaws_template_d_qwen.py` is deprecated: it wrote canned label-templated text into `answer`. Its outputs are not QA data.
- Local Python is 3.9: fine for mock/smoke, but cluster code paths assume Python 3.10+ (shared-mount cluster) / 3.12.4 (module+venv cluster).
- Gated models (Gemma) require an HF token with access; set it in the environment before `snapshot_download`.
