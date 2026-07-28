# Results

Generated QA output that is worth keeping. Only runs that pass
`scripts/check_template_d.py` cleanly and represent a real step forward belong
here. Superseded runs, and runs with known rule violations, are not committed
even for reference -- the failure is described in the commit message instead of
being archived as data.

Subdirectories are named `outputs after <what was fixed> - M-D-YYYY`, so the
folder itself records which change the batch is demonstrating.

Every record is annotation-only: no frame, image, or video is ever passed to
the model. Full provenance (exact prompt, raw model output including its
reasoning, source annotation values, validation status) is in
`qa_records.jsonl`, which is the source of truth. `qa_pairs.jsonl` and
`qa_pairs.md` are extracted views for machines and humans respectively;
regenerate them with `python3 scripts/extract_qa_pairs.py <dir>/qa_records.jsonl`.

## `outputs after skill-level gloss and anatomy rule - 7-26-2026`

Template D, all five templates, across all three JIGSAWS tasks: 75 records
from 5 trials each of Suturing, Knot Tying and Needle Passing, one question per
whole video.

| | |
| --- | --- |
| Model | `qwen3.6-35b-a3b-iq4xs` (4-bit GGUF) via local Ollama |
| Sampling | temperature 0.7, `max_new_tokens` 9000, `num_ctx` 12288 |
| Validation | 75/75 valid, 0 rejected, 0 retries fired, 0 issues under `check_template_d.py` |

The first batch generated with the whole prompt stack settled, so all 75
records share one prompt state. Earlier batches did not: adopting the
skill-level gloss changed the prompt mid-project, which left the previous
25-record Suturing run inconsistent with everything generated after it.

What this batch demonstrates, in the order the problems were found and fixed:

1. **Self-contained questions.** D2/D3/D4 used to say "among these three
   feedback points" without naming them. The three points are now computed in
   code from the three lowest GRS subscores, with deterministic tie-breaking,
   and substituted into the question text.
2. **No hidden-rubric references.** Answers used to say things like "this
   received the lowest rating on your technical rubric," which a reader who
   cannot see the scores has no way to judge. Score references now live only in
   the rationale.
3. **Natural coaching voice.** Every answer once opened with the same sentence.
   Fixed with a hard rule plus a mechanism that threads a batch's already-used
   opening sentences into later prompts -- each record is an independent model
   call with no memory of the others, so a static prompt alone cannot prevent
   repetition.
4. **"and why" in the D2 question**, after review feedback that it otherwise
   read as multiple choice.
5. **No fabricated behaviour.** D1 answers were inventing specifics the
   annotations cannot support ("you are applying unnecessary downward
   pressure"). Hard rule 1 now states that forward-looking phrasing is not an
   exemption from the no-fabrication rule.
6. **No invented patient anatomy.** Answers referred to `fascia`, `wound
   margin` and `postoperative` complications. JIGSAWS is a bench-top exercise
   with no anatomy labels at all, so hard rule 4 now permits the general
   clinical principle behind a skill while barring claims that this trial
   involved specific anatomy or produced a patient outcome.
7. **Skill level glossed.** The prompt now says `skill_level: I (intermediate,
   10-100 hours robotic surgical practice)` rather than a bare `I`. 58% of
   Template D records reason about skill level, and an unglossed code had
   already produced an answer calling level I "novice".

### Known limitations

- Generated locally on a 4-bit quantisation, not at full precision on the
  cluster. Not yet reproduced there, so whether quantisation costs anything in
  coaching quality is untested.
- Nothing verifies that the coaching advice is clinically sound. The checker
  confirms records are grounded in the labels and free of the rule violations
  it knows about; it cannot judge medicine. Expert review is required.
- Type C templates are not in this batch. They now have pinned annotation-only
  wording but have never been generated with it.
