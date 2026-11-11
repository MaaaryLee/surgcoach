# Qwen3.8 zero-shot, annotation-only QA (2026-11-09)

Qwen3.8-27B (Q4_K_M GGUF, llama.cpp, run text-only with `--no-mmproj`) replaces Qwen3.6 both as the answer generator and as the reviewer. It is tested on every earlier annotation-only question set:

- **JIGSAWS:** D1, D2, D5 and the VTS locate questions
- **MultiBypass140:** D1, D2, D5, plus B1, B3, B4, B5, B6
- **Endoscapes2023:** B1, B2, B3, B5, B7

That is 741 questions: 50 per type, except where the gated eligibility rules leave fewer (JIGSAWS D2 20, D5 5; MB140 D2 31, D5 35).

**Start with [`overview-for-review.md`](overview-for-review.md)** for the results table, the takeaways and seven representative questions.

## Zero-shot: what was removed from the earlier pipelines
- The "Examples of the voice" block in the MB140 D system prompt.
- The "openings already used" / recent-answer-openings lists (JIGSAWS D, B).
- Every retry-with-feedback loop, the audit judges, and their self-tests and calibration.

Kept as before: the system prompts, the per-question answer plans and inline rules, and the B2/B4 "Begin the answer with 'No'" instructions.

Not removed, and it matters for the scores: the MB140 answer plans still give a filled-in sentence. Every D2 plan says, for example, `Name event 1 as the one to address first ... as in "You should address the mechanical injury and bleeding at 0:10:20, during jejunojejunal stapling, first."`, and some D5 plans do the same. These are built from the question's own facts, not taken from another question, so the run is still zero-shot in the strict sense. But the model mostly copies them, which is a large part of why MB140 D2 scores 31/31.

Each question is one request. The identical request is resent at most twice, and only on a transport or parse failure.

## Reviewer
The reviewer is Prompt A of [`Prompts_And_Pipeline/llm-rubric-v5.md`](../../Prompts_And_Pipeline/llm-rubric-v5.md), scored 1–5, where ≤3 counts as a defect. It scores three checks:

- `label`
- `localization` (only where it applies)
- `scope`

The reviewer is given:

- the setting and dataset;
- the recorded labels and what is not recorded;
- the source annotation;
- the annotation lines the answering model saw, without the question's answer plan.

It runs at temperature 0 with thinking on.

## Files
| File | What it holds |
|---|---|
| `summary.csv` / `summary.json` | Per dataset × type: n, unanswered, reviewed, mean and distribution per check, defects, all-5s. |
| `records/<dataset>_<type>.jsonl` | Question, answer, scores, reviewer notes and annotation facts. Reasoning traces and full prompts are omitted. |
| `representative-examples.md` | One seeded typical all-5 answer per type, plus every defect. |
| `scripts/` | The run: unit builders (`build_units_{d,b,vts}.py`), `zs_run.py` (generate + judge), `zs_run.sbatch` (Slurm, a6000) and `zs_report.py`, alongside copies of the pipeline modules they import. |

Run on UNC CS oprime/megatron with Slurm account gedas, partition a6000 and one GPU per job.

## Caveats
- The same model generates and reviews.
- D and VTS answers follow detailed answer plans, so their scores mostly measure plan-following.
- No reviewer self-test on deliberately broken answers has been run.
