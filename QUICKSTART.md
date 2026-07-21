# Quickstart: Generating Surgical QA Pairs From Annotations

This guide assumes you have **never used this repo** and never used an LLM to generate QA pairs. It takes you from zero to a working run, then to batches of runs. No prior cluster experience is assumed — where cluster specifics matter, ask a labmate for your login details and Slurm account name (they are deliberately not written in this repo).

## 1. What this project does, in plain words

We take a surgical training dataset called **JIGSAWS** (recordings of trainees doing practice suturing on a bench-top pad). Each recorded trial comes with expert **annotations**: a skill level, six 1-5 skill scores (the "GRS subscores"), and a list of **gestures** — labeled time spans like "frames 371-590: orienting needle."

We feed ONLY those annotations (never the video) to a large language model, along with a system prompt that defines question **templates A-D** (perception, safety, skill assessment, coaching). The model writes back a **QA pair** — a `question`, an `answer`, and a `rationale` — as JSON. Thousands of these become training/evaluation data for surgical-coaching AI.

Two rules explain most of the design:

1. **Honesty about evidence.** The model never saw the video, so it must never claim it did. Templates that would require seeing things (or labels we don't have) are *refused*, not faked. On JIGSAWS that means: **A3, C1, C2, C3, C6, C7, D1-D5 work; A1, A2, A4, all of B, C4, C5 do not** (no supporting labels exist). If you need Type B, that requires a dataset with safety labels — not in this repo yet.
2. **Provenance on everything.** Every output record says whether a real model produced it (`backend: "real"`) or it was a test placeholder (`backend: "mock"`, watermarked). Never mix the two.

## 2. Your first run — 5 minutes, on your laptop, no installs

Mock mode exercises the whole pipeline (reading annotations, building prompts, validating output) with a fake watermarked "model". It needs only stock Python 3.

```bash
cd ~/surgcoach
python3 scripts/run_annotation_qa_jigsaws.py \
  --dataset-root outputs/local_smoke_fixture/Suturing \
  --system-prompt Prompts_And_Pipeline/system-prompt-A-D.md \
  --trial-id Suturing_Z999 \
  --backend mock \
  --output-dir /tmp/my_first_run
```

You should see 11 lines ending in `valid_mock`, then a JSON summary. Look at one record:

```bash
python3 -c "import json; r=[json.loads(l) for l in open('/tmp/my_first_run/qa_records.mock.jsonl')][6]; print(json.dumps(r, indent=2))"
```

Things to notice in the record:

- `source_annotation` — the real inputs (scores, gesture, frame range).
- `model.prompt` — exactly what would be sent to the LLM.
- `qa` — the question/answer/rationale. In mock mode the answer is a `[MOCK OUTPUT ...]` watermark; a real run has model-written text here.
- `model.backend` / `validation_status` — provenance. Mock output goes to `*.mock.jsonl` and can never be mistaken for data.

`Suturing_Z999` is a made-up smoke-test trial. Real trials look like `Suturing_B001` and live in the dataset on the cluster.

## 3. Your first REAL run

You need three things:

1. **GPU access** — this lab runs jobs on a Slurm cluster; get your login and the project's Slurm account name from your PI/labmates. The model is ~35B parameters (MoE, 3B active) and will not run on a laptop.
2. **A Hugging Face account + token** — create one at huggingface.co → Settings → Access Tokens, then on the cluster run `huggingface-cli login` (or `export HF_TOKEN=...`). The generator model is public: [Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B).
3. **The dataset path** — the JIGSAWS Suturing folder on your cluster (contains `meta_file_Suturing.txt`, `transcriptions/`, `video/`). Ask a labmate where it is mounted.

Install dependencies (in a venv, or per your cluster's convention — see [docs/environment-and-libraries.md](docs/environment-and-libraries.md) section 3):

```bash
pip install --extra-index-url https://download.pytorch.org/whl/cu124 torch==2.6.0+cu124
pip install -r requirements-annotation.txt
```

Then, on a GPU node (directly, or wrapped in your cluster's sbatch — `scripts/run_annotation_qa_jigsaws_oprime.sbatch` is a template; edit its paths for your setup and pass your account with `sbatch --account=...`):

```bash
python3 scripts/run_annotation_qa_jigsaws.py \
  --dataset-root <path-to>/JIGSAWS/Suturing \
  --system-prompt system-prompt-A-D.md \
  --trial-id Suturing_B001 \
  --templates D1 \
  --backend real \
  --output-dir results/first_real_run
```

Start with `--templates D1` and one trial: it's one model call per gesture span, so you get feedback fast. Success looks like `qa_records.jsonl` (note: **no** `.mock.`) with `"validation_status": "valid"` and a model-written answer. Common first-run failures:

| Symptom | Cause / fix |
| --- | --- |
| `401` / gated repo error downloading model | HF token not set on the cluster |
| `KeyError: ... architecture` or unrecognized model type | transformers too old — upgrade to latest, or `pip install --no-deps git+https://github.com/huggingface/transformers.git` |
| CUDA out of memory | use more/bigger GPUs (`--device-map auto` shards across all visible GPUs) |
| `rejected ... question_mismatch` | model didn't echo the exact D-template question; it's dropped, not silently kept — rerun or inspect `raw_output` |
| Slow first run | it's downloading ~35B parameters into the HF cache; later runs reuse the cache |

## 4. Scaling to 50-100 runs

One "run" = one trial × the templates you request. The runner does one trial per invocation, so batching is a loop. List the real trials first:

```bash
awk '{print $1}' <path-to>/JIGSAWS/Suturing/meta_file_Suturing.txt
```

Then loop (shell example; on Slurm, submit one job per trial the same way, or use a job array):

```bash
for TRIAL in $(awk '{print $1}' <root>/meta_file_Suturing.txt); do
  python3 scripts/run_annotation_qa_jigsaws.py \
    --dataset-root <root> \
    --system-prompt system-prompt-A-D.md \
    --trial-id "$TRIAL" \
    --templates C1,C2,C3,C6,C7 \
    --backend real \
    --output-dir "results/c_batch/$TRIAL"
done
```

JIGSAWS Suturing has ~39 trials with ~20 gesture spans each, so even one template across all trials yields hundreds of QA pairs — pick templates and `--gesture-id` filters to hit your target count.

Merge and count afterwards:

```bash
python3 - <<'EOF'
import json, glob
recs = [json.loads(l) for f in glob.glob("results/c_batch/*/qa_records.jsonl") for l in open(f)]
valid = [r for r in recs if r["validation_status"] == "valid"]
from collections import Counter
print(Counter(r["template_id"] for r in valid))
json.dump(valid, open("results/c_batch/merged_valid.json","w"), indent=2)
EOF
```

**About Type B (and C4/C5):** don't loop over these — the runner refuses them on JIGSAWS by design, because no safety/bimanual/targeting labels exist and generating them would be fabrication. Producing B-template QA pairs requires adding an annotation loader for a safety-labeled dataset (e.g., Endoscapes2023 or MultiBypass140); that is future work, not a flag.

## 5. Rules that keep the data trustworthy

1. Never copy mock/watermarked output into a real data file. Mock lives in `*.mock.jsonl`, real in `qa_records.jsonl`.
2. Never write your own text into the `answer` field. Answers come from the model; annotations go in `source_annotation`.
3. Filter on `validation_status == "valid"` before using anything downstream.
4. If a template is refused, the fix is a dataset with the right labels — never prompt tricks.

More depth: [docs/environment-and-libraries.md](docs/environment-and-libraries.md) (environments, libraries, models) and the README (project layout, deprecated scripts to avoid).
