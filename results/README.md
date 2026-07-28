# Results

Generated QA output that is worth keeping. Only runs that pass
`scripts/check_template_d.py` cleanly and represent a real step forward belong
here. Superseded runs, and runs with known rule violations, are not committed
even for reference -- the failure is described in the commit message or in the
notes below instead of being archived as data.

Subdirectories are named `outputs after <what was fixed> - M-D-YYYY`, so the
folder itself records which change the batch is demonstrating.

Every record is annotation-only: no frame, image, or video is ever passed to
the model. Full provenance (exact prompt, raw model output including its
reasoning, source annotation values, validation status) is in
`qa_records.jsonl`, which is the source of truth. `qa_pairs.jsonl` and
`qa_pairs.md` are extracted views for machines and humans respectively;
regenerate them with `python3 scripts/extract_qa_pairs.py <dir>/qa_records.jsonl`.

## `outputs after and-why question fix - 7-26-2026`

Template D2 (Feedback With Priority), 15 records: 5 trials each of Suturing,
Knot Tying, and Needle Passing, one question per whole video.

| | |
| --- | --- |
| Model | `qwen3.6-35b-a3b-iq4xs` (4-bit GGUF) via local Ollama |
| Sampling | temperature 0.7 |
| Validation | 15/15 valid, 0 issues under `check_template_d.py` |

This is the batch after the last round of review feedback. What it
demonstrates, in the order the problems were found and fixed:

1. **Self-contained questions.** The question used to read "among these three
   feedback points" without naming them, which nobody outside the pipeline
   could answer. The three points are now computed in code from the three
   lowest GRS subscores and substituted into the question text.
2. **No hidden-rubric references.** Answers used to say things like "this
   received the lowest rating on your technical rubric," which is ungradable
   by a reader who cannot see the scores. Answers now give direct clinical
   guidance and keep every score reference in the rationale.
3. **Natural coaching voice.** Every answer used to open with the identical
   sentence. Fixed with a hard rule plus a mechanism that threads a batch's
   already-used openers into later prompts, since each record is an
   independent model call with no memory of the others.
4. **"and why" in the question** (the change this folder is named for).
   Without it the question reads as multiple choice, even though the answers
   were already explaining their reasoning.

### Known limitations

- Generated locally on a 4-bit quantisation, not at full precision on the
  cluster. Not yet reproduced there.
- Nothing verifies that the coaching advice is clinically sound. The checker
  only confirms records are grounded in the labels and free of the rule
  violations it knows about. Expert review is still required.
- Some answers include general clinical framing (why a skill matters in
  surgery) that goes beyond what JIGSAWS annotations strictly contain, since
  JIGSAWS has no anatomy labels at all. Whether that is acceptable teaching
  context is an open question.
