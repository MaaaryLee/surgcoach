# JIGSAWS coaching QA: Template D1, D2 and D5

The pipeline asks one written coaching question per JIGSAWS trial: D1, D2 or D5. The answer
reads like a teacher's feedback to the trainee. Every detail in it comes from:

- the JIGSAWS rubric (Gao et al. 2014, Table 3);
- the trial's ratings;
- for one case, the trial's gesture timing.

The model is Qwen3.6-35B-A3B (Q4_K_M) on llama.cpp's llama-server, with thinking on.

## The questions

| | Question, verbatim (the trial's span is filled in) | Asked only when | Trials of 103 |
|---|---|---|---|
| D1 | If you were the supervising surgeon, based only on the events occurring during [a certain video span], what feedback would you give on the trainee's tissue handling, instrument/needle handling, economy of motion, and flow of operation? | Overall performance is 1, 3 or 5 | 56 |
| D2 | Given the trainee's performance during [a certain video span], among tissue handling, instrument/needle handling, economy of motion, and flow of operation, which area should the trainee improve first, why, and what should the trainee do immediately to address it? | One technique area is alone lowest, at 1 or 3 | 20 |
| D5 | Based on this trainee's performance, what is their strongest technical skill, and why does it matter? | One technique area is alone highest, at 5 | 5, all Suturing, all flow of operation |

The rubric describes only levels 1, 3 and 5, so every question rests on a described level.
A tie for lowest or highest means no D2 or D5.

## What an answer may say

- **Levels.** An area rated 1, 3 or 5 is described in its rubric description's own words.
  Nothing is said about how an area rated 2 or 4 went. No level is compared with the ones
  above or below it.
- **Area names.** Each area has one fixed name:
  - "the way you handled the tissue"
  - "your control of the needle and suture"
  - "how efficiently you moved"
  - "how you planned your course of operation"
- **D1.** The answer opens with the Overall performance verdict (very poor, competent or
  clearly superior). Then it says only what stood out:
  - areas rated 5, with their level 5 description;
  - areas rated 4, named only.

  It stays under 50 words.
- **D2.** In three or four sentences, the answer gives:
  - the weakest area, as what to improve first;
  - what that area looked like;
  - where the extra time went, when timing applies;
  - the goal: the next described level, stated plainly.
- **D5.** The answer gives the strongest area, its level 5 description, and why it matters.
  Table 3 gives no reasons, so the reason is built from that area's own descriptions. It is
  marked as an adaptation: `WHY_IT_MATTERS` in `jigsaws_paper_reference.py`.
- **Never** in an answer:
  - practice or technique suggestions;
  - numbers;
  - the rating system;
  - the trainee's experience level;
  - the final product;
  - knowledge from outside the rubric.

The model never sees the GRS total, the self-reported skill level or Quality of final
product. It sees Overall performance only for D1.

## Timing, the one step-level detail

`validate_jigsaws_kinematic_measures.py` and `validate_jigsaws_area_measures.py` test which
gesture and kinematic measures track which rating, across every trial. Only trial time
tracks one, Time and motion:

| Task | Spearman ρ | ρ with the other three ratings held fixed | Used |
|---|---|---|---|
| Suturing, 39 trials | −0.49 (p 0.002) | −0.43 (p 0.006) | yes |
| Knot Tying, 36 trials | −0.81 (p < 0.001) | −0.45 (p 0.007) | yes |
| Needle Passing, 28 trials | −0.14 (p 0.46) | 0.10 (p 0.62) | no |

No measure tracks respect for tissue, suture/needle handling or flow of operation. So
answers say nothing step-level about those areas.

Timing goes into an answer only under all of these conditions:

- it is a D2 answer about Time and motion;
- Time and motion is rated 3 or lower;
- the task is Suturing or Knot Tying;
- the run took at least 1.25 times the reference median.

The answer then gets one sentence. It says how much longer the run took than "the runs
judged most efficient on this task", and names at most two steps that took the extra time.
Those reference runs are the task's other trials rated 4 or 5 on Time and motion, or 3 and
above when fewer than three trials are rated 4 or 5. The exact numbers go into the
code-written rationale, never the answer.

Comments name the validation runs, e.g. `results/area measure validation - 10-21-2026`. Those
outputs are kept outside the repo, and the two scripts regenerate them.

## Files

| File | Role |
|---|---|
| `scripts/build_jigsaws_enriched_pilot.py` | Reads each trial's ratings, gesture transcription and kinematics, using `scripts/localize_events.py`. Writes `enriched_trial_inputs.jsonl`, including the trial's timing against the reference trials. |
| `scripts/run_jigsaws_enriched_d.py` | Builds each prompt, asks the model, checks the answer, retries, and writes the records and rationales. |
| `scripts/jigsaws_template_d_plan.py` | The question wording, and the rules above for which trial gets which question. |
| `scripts/jigsaws_paper_reference.py` | The paper's rubric, task descriptions and gesture names, verbatim. Adaptations are marked: the knot wording restated for Suturing and Needle Passing, and `WHY_IT_MATTERS`. |
| `Prompts_And_Pipeline/system-prompt-jigsaws-enriched-d.md` | The system prompt. |
| `scripts/verify_jigsaws_step_evidence.py` | Recomputes every timing number in the rationales from the raw files, with code independent of the builder. |
| `scripts/run_jigsaws_enriched_d.sbatch` | The Slurm job on oprime: build, answer, verify. |
| `scripts/validate_jigsaws_kinematic_measures.py`, `scripts/validate_jigsaws_area_measures.py` | The validation behind the timing rule. |

## Checks on every answer

1. **Pattern checks** (`check_answer`) compare the answer with what its prompt carried:
   - required names, the description's own words and the verdict word;
   - length;
   - numbers, labels and comparisons;
   - suggestions and outside knowledge;
   - any timing or steps the prompt did not give.
2. **Reviewer.** A second call to the same model audits every sentence against the evidence
   (`JUDGE_SYSTEM`, six rules). When Needle_Passing_C003 D1 is in the run, the reviewer is
   first tested on four paragraphs with known verdicts, and the result is recorded.
3. **Retries.** A rejected draft goes back to the model with its problems, up to five
   retries.
4. **Rationale.** Code writes the rationale from the evidence, and the verifier recomputes
   its numbers.

## Running it

Submit from a run directory that holds `scripts/` and `Prompts_And_Pipeline/`:

```bash
sbatch scripts/run_jigsaws_enriched_d.sbatch
```

That runs the eight trials in `DEFAULT_TRIAL_TEMPLATES`. For other trials, export the list,
since `sbatch --export=VAR=a,b` splits on the commas:

```bash
export TRIAL_TEMPLATES=Suturing_C001:D5,Suturing_B001:D2,Needle_Passing_C003:D1
sbatch --export=ALL scripts/run_jigsaws_enriched_d.sbatch
```

A dry run needs no model. It writes every prompt and rationale to `prompts_dry_run.txt`:

```bash
python3 scripts/build_jigsaws_enriched_pilot.py --jigsaws-root <JIGSAWS root> \
  --trials Suturing_C001,Suturing_B001 --output-dir out
python3 scripts/run_jigsaws_enriched_d.py --inputs out/enriched_trial_inputs.jsonl \
  --trial-templates Suturing_C001:D5,Suturing_B001:D2 --output out/qa_records.jsonl --dry-run
```

These outputs are written next to `qa_records.jsonl`:

- `qa_records.jsonl`: every record with full provenance. That covers the ratings, plan,
  evidence, prompt, raw output, reasoning, reviewer output, rejected drafts and rationale.
- `qa_slim.json`: for each kept answer, the trial, template, timestamp, frames, question and
  answer, for temporal localization.
- `review.md`: the answers, for reading.
- `run_summary.json`: the counts and the reviewer self-test.
- `step_verification.md`: the independent timing check.

## Status

The last run was job 35897, on 2026-10-21. It kept 7 of the 8 default trials, the reviewer
self-test passed all four cases, and the timing check passed.

One rule is not in the code yet: the rating labels. The code still bans them ("tissue
handling", "time and motion" and so on) in the system prompt, the answer plan, the `LABELS`
check, the retry hint and the reviewer. That ban caused job 35897's one rejection,
Needle_Passing_F003 D2:

- Its goal, in the rubric's own words, says "tissue handling", which the label check
  rejected.
- The prompt's rewording, "careful handling of the tissue", was then flagged by the reviewer
  as a paraphrase.

Since 2026-10-29 the original labels are allowed as they are, and rewording them is fine too.
