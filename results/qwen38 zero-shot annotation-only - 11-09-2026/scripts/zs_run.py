#!/usr/bin/env python3
"""Zero-shot run: Qwen3.8 answers each unit once, then reviews each answer with LLM rubric v5.

generate: one request per unit (system + user prompt exactly as in the unit). No retries on
          content, no feedback, no examples. A request is re-sent only when the HTTP call or
          the reply's JSON wrapper fails (max 2 more tries), and that is recorded.
judge:    LLM rubric v5, Prompt A (Prompts_And_Pipeline/llm-rubric-v5.md), filled with the
          unit's [GROUND_TRUTH] and the answer. Prompt B is not run: no pipeline here writes an
          inferred_cause field.

python3 -I scripts/zs_run.py --units units/JIGSAWS_D1.jsonl ... --out outputs/<job> \
        --host http://127.0.0.1:19xxx --workers 4
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RUBRIC = Path(__file__).resolve().parent.parent / "Prompts_And_Pipeline" / "llm-rubric-v5.md"
CHECKS = ("label_score", "localization_score", "scope_score")


def chat(host: str, system: str, user: str, temperature: float, max_tokens: int, thinking: bool) -> dict:
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    body = json.dumps({"messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                       "stream": False, "chat_template_kwargs": {"enable_thinking": thinking}}).encode()
    req = urllib.request.Request(host.rstrip("/") + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=1800) as r:
        res = json.loads(r.read().decode())
    msg = res["choices"][0]["message"]
    return {"content": msg.get("content") or "", "reasoning": msg.get("reasoning_content"),
            "finish_reason": res["choices"][0].get("finish_reason"), "usage": res.get("usage")}


def json_object(text: str) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(cleaned[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")
    return obj


def dig(obj, dotted: str):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            raise ValueError(f'reply has no "{dotted}" field')
        obj = obj[part]
    return obj


def extract_answer(unit: dict, content: str) -> str:
    fmt = unit.get("answer_format", "json_answer")
    if fmt == "text":
        return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    value = dig(json_object(content), unit.get("answer_key") or "answer")
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("answer field is empty")
    return value.strip()


def generate(unit: dict, host: str) -> dict:
    g = unit.get("generation", {})
    started, tries, errors, reply, answer = time.time(), 0, [], None, None
    for tries in range(1, 4):
        try:
            reply = chat(host, unit["system_prompt"], unit["user_prompt"], g.get("temperature", 0.6),
                         g.get("max_tokens", 8000), g.get("thinking", True))
            answer = extract_answer(unit, reply["content"])
            break
        except (ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            # A content reply that parsed badly is not re-asked with feedback: the same request
            # is sent again unchanged, which zero-shot allows.
    return {"unit_id": unit["unit_id"], "dataset": unit["dataset"], "template_id": unit["template_id"],
            "question": unit["question"], "answer": answer,
            "status": "answered" if answer is not None else "no_answer",
            "errors": errors, "requests": tries,
            "raw_output": reply["content"] if reply else None,
            "reasoning": reply["reasoning"] if reply else None,
            "finish_reason": reply["finish_reason"] if reply else None,
            "usage": reply["usage"] if reply else None,
            "elapsed_seconds": round(time.time() - started, 1), "meta": unit.get("meta", {})}


def prompt_a_template() -> str:
    text = RUBRIC.read_text(encoding="utf-8")
    section = text.split("## Prompt A", 1)[1].split("## Prompt B", 1)[0]
    return re.search(r"```text\n(.*?)\n```", section, flags=re.DOTALL).group(1)


def as_text(value, sep: str = "\n") -> str:
    if isinstance(value, (list, tuple)):
        return sep.join(as_text(v, sep) for v in value)
    if isinstance(value, dict):
        return sep.join(f"{k}: {as_text(v, '; ')}" for k, v in value.items())
    return str(value)


def indent(text, pad: str = "    ") -> str:
    text = as_text(text)
    return "\n".join(pad + line if line.strip() else "" for line in text.strip().splitlines())


def judge_prompt(template: str, unit: dict, answer: str) -> str:
    gt = unit["ground_truth"]
    block = "\n".join([
        "[GROUND_TRUTH]",
        f"  setting: {gt['setting']}",
        f"  dataset: {unit['dataset']}",
        f"  {as_text(gt['recorded_label'], '; ')}:",
        indent(gt["recorded"]),
        f"  not recorded in this dataset: {as_text(gt['not_recorded'], '; ')}",
        *([f"  localization: NOT APPLICABLE (the question itself names the span or unit)"]
          if gt.get("localization", "").upper().startswith("NOT") else []),
        f"  source annotation: {as_text(gt['source_annotation'], '; ')}",
        "  the lines the answering model was given:",
        indent(unit["user_prompt"]),
        "",
        "[RECORD]",
        f"  question: {unit['question']}",
        f"  answer: {answer}",
    ])
    start = template.index("[GROUND_TRUTH]")
    end = template.index("Return valid JSON only")
    return template[:start] + block + "\n\n" + template[end:]


def judge(unit: dict, gen: dict, host: str, template: str) -> dict:
    out = {"unit_id": unit["unit_id"], "dataset": unit["dataset"], "template_id": unit["template_id"]}
    if gen["answer"] is None:
        return {**out, "judged": False, "reason": "no answer to review"}
    prompt = judge_prompt(template, unit, gen["answer"])
    errors, reply = [], None
    for _ in range(3):
        try:
            reply = chat(host, "", prompt, 0.0, 12000, True)
            obj = json_object(reply["content"])
            scores = {}
            for c in CHECKS:
                v = obj.get(c)
                if v is not None and v not in (1, 2, 3, 4, 5):
                    raise ValueError(f"{c} out of range: {v!r}")
                scores[c] = v
            return {**out, "judged": True, **scores, "uncertain": obj.get("uncertain", []),
                    "evaluator_notes": obj.get("evaluator_notes", ""), "judge_errors": errors,
                    "judge_reasoning": reply.get("reasoning"), "judge_prompt": prompt}
        except (ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    return {**out, "judged": False, "reason": "; ".join(errors),
            "judge_raw": reply["content"] if reply else None, "judge_prompt": prompt}


def run_pool(fn, items, workers):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        yield from ex.map(fn, items)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--phase", choices=["both", "generate", "judge"], default="both")
    ap.add_argument("--model-id", default="ggml-org/Qwen3.8-27B-GGUF:Q4_K_M")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    units = [json.loads(l) for p in args.units for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_id = {u["unit_id"]: u for u in units}
    gen_path, judge_path = args.out / "generations.jsonl", args.out / "reviews.jsonl"

    if args.phase in ("both", "generate"):
        done = {json.loads(l)["unit_id"] for l in gen_path.read_text().splitlines()} if gen_path.exists() else set()
        todo = [u for u in units if u["unit_id"] not in done]
        print(f"generate: {len(todo)} of {len(units)} units", flush=True)
        with gen_path.open("a", encoding="utf-8") as fh:
            for i, rec in enumerate(run_pool(lambda u: generate(u, args.host), todo, args.workers), 1):
                rec["model_id"] = args.model_id
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"[gen {i}/{len(todo)}] {rec['unit_id']}: {rec['status']} in {rec['elapsed_seconds']}s",
                      flush=True)

    if args.phase in ("both", "judge"):
        template = prompt_a_template()
        gens = {json.loads(l)["unit_id"]: json.loads(l) for l in gen_path.read_text().splitlines() if l.strip()}
        done = {json.loads(l)["unit_id"] for l in judge_path.read_text().splitlines()} if judge_path.exists() else set()
        todo = [gens[k] for k in gens if k not in done and k in by_id]
        print(f"judge: {len(todo)} answers", flush=True)
        with judge_path.open("a", encoding="utf-8") as fh:
            for i, rec in enumerate(run_pool(lambda g: judge(by_id[g["unit_id"]], g, args.host, template),
                                             todo, args.workers), 1):
                rec["judge_model_id"] = args.model_id
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                s = " ".join(f"{c.split('_')[0]}={rec.get(c)}" for c in CHECKS) if rec.get("judged") else rec.get("reason")
                print(f"[judge {i}/{len(todo)}] {rec['unit_id']}: {s}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
