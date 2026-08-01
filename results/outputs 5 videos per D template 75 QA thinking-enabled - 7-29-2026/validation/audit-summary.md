# Validation Summary

Status: **PASS**

- Records: 75
- Valid: 75
- Rejected: 0
- Unique QA IDs: 75
- Per task: 25
- Per template: 15
- Unique videos per task/template group: 5
- Thinking enabled, required, and verified: 75/75
- Non-empty reasoning-trace length: 7,704-25,410 characters; mean 15,023
- Exact template questions: 75/75
- Timestamp spans: 75/75
- Rule 11 answer leaks: 0
- Template D quality-check issues: 0

The independent audit also confirmed that every provenance record uses real inference with `ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M`, that every prompt hash matches the included prompt copy, and that each task summary reports 25 valid records with `thinking_verified: 25`.
