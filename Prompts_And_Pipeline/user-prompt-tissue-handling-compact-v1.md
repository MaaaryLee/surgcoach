# User Prompt: Tissue Handling Compact QA v1

Use this prompt when the task asks for a tissue-handling assessment but the output should stay in the compact SurgCoach QA shape.

```text
Rate the trainee's tissue handling in this clip from 1-5 and provide one improvement.

Return exactly one valid JSON object with only these keys:
question, answer, rationale.

Question:
Rate the trainee's tissue handling in this clip from 1-5. Provide visible evidence and one improvement.

Answer rules:
- Put the rating and the short trainee-facing assessment in the answer.
- Use the format: "<rating>/5: <one concise clinical assessment>."
- Keep the answer plainspoken, attending-style, and actionable.

Rationale rules:
- Put all support details in rationale, not in separate JSON fields.
- Include the rating scale in prose: 1=poor and 5=excellent.
- Include the exact evidence frame or frame range.
- Include visible evidence from the clip.
- Include one concrete improvement target.
- Be clinically specific but do not invent tissue tearing, injury, bleeding, anatomy, or complications that are not visible.
- If the visual evidence is limited, say so briefly while still giving the best supported assessment.

JSON shape:
{
  "question": "Rate the trainee's tissue handling in this clip from 1-5. Provide visible evidence and one improvement.",
  "answer": "<rating>/5: <one concise clinical assessment>.",
  "rationale": "Using a 1-5 scale where 1=poor and 5=excellent, <explain the rating using the visible evidence at frames/timestamp X and include one specific improvement>."
}
```
