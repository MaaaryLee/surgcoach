# Template D Thinking-Enabled Rerun

This folder contains the complete rerun of JIGSAWS Template D1-D5 QA generation.

## Result

- 75 real model-generated QA pairs
- 75 valid, 0 rejected
- 25 pairs per surgery task
- 15 pairs per question template
- 5 unique videos for every task/template combination
- Exact D1-D5 template questions
- Video timestamps rather than frame ranges
- Thinking requested and verified by a non-empty reasoning trace for all 75 records
- Scores, ratings, rubrics, annotations, assessments, and performance metrics do not appear in the answers; annotation details remain in the rationales

## Tasks and Videos

- Suturing: `Suturing_B001` through `Suturing_B005`
- Needle Passing: `Needle_Passing_B001` through `Needle_Passing_B004`, plus `Needle_Passing_C001`
- Knot Tying: `Knot_Tying_B001` through `Knot_Tying_B004`, plus `Knot_Tying_C001`

Each of D1, D2, D3, D4, and D5 was run once for every listed video.

## Model and Prompts

- Model: `ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M`
- Inference host: UNC Mirage
- Both system prompts were active simultaneously:
  - `system-prompt-A-D.md`
  - `system-prompt-jigsaws-v1.2.md`
- Active general prompt SHA-256: `6546197735f320b23bddb6eceb29ca489bcf8c73dee3f989010ceab5796ee3ea`
- Active JIGSAWS prompt SHA-256: `ce62642c0754332a1d650cbf7dff69dc10dda15f96beb6a5d50bbf29fbd0ec74`

Copies of both prompts and the exact template-question file are in `prompts_used/`.

## Files

- `by_template/D1.md` through `by_template/D5.md`: the requested human-readable QA files, 15 pairs each
- `Suturing/qa_pairs.md`, `Needle_Passing/qa_pairs.md`, and `Knot_Tying/qa_pairs.md`: the same pairs grouped by surgery task
- Each task's `qa_records.jsonl`: full provenance, model request settings, raw outputs, and verified reasoning traces
- Each task's `run_summary.json`: counts and prompt hashes
- `validation/`: validation results

The earlier non-thinking output folders were not modified.
