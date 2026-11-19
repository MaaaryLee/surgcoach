#!/usr/bin/env python3
"""Zero-shot video QA. Each model answers each question from video frames alone; a separate
judge model then scores every answer against the annotation (vlm-zero-shot-judge-v1.md, the
video version of LLM rubric v5).

generate  one request per question: system prompt, the procedure in one line, the frames in
          order with each frame's time in the recording, then the question. No annotations,
          no answer plan, no examples, no feedback. The identical request is re-sent at most
          twice, and only when the call fails or comes back empty.
judge     one request per answer to an OpenAI-compatible endpoint serving the judge model
          (text only). It sees the question, the annotation block and what the model was
          shown; it never sees the frames.

python3 -I scripts/zs_video/zs_video_run.py generate --units video_units/*.jsonl \
    --out outputs/qwen38-27b --host http://127.0.0.1:19123 --model-id "Qwen3.8-27B Q4_K_M" --thinking
python3 -I scripts/zs_video/zs_video_run.py judge --units video_units/*.jsonl \
    --answers outputs/*/generations.jsonl --host http://127.0.0.1:19456 --judge-id "gemma-3-27b-it Q4_K_M"
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_PROMPT = ROOT / "Prompts_And_Pipeline" / "vlm-zero-shot-system-prompt.md"
JUDGE_PROMPT = ROOT / "Prompts_And_Pipeline" / "vlm-zero-shot-judge-v1.md"
CHECKS = ("label_score", "localization_score", "scope_score")


# --------------------------------------------------------------------------- transport
def post(host: str, body: dict, timeout: int = 3600) -> dict:
    req = urllib.request.Request(host.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read().decode())
    msg = res["choices"][0]["message"]
    return {"content": msg.get("content") or "", "reasoning": msg.get("reasoning_content"),
            "finish_reason": res["choices"][0].get("finish_reason"), "usage": res.get("usage")}


def strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.split("</think>")[-1].strip()


def text_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    if not m:
        raise SystemExit(f"no ```text block in {path}")
    return m.group(1)


def load_units(paths: list[Path]) -> list[dict]:
    return [json.loads(l) for p in paths for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if path.exists() else []


def run_pool(fn, items, workers):
    """Results as they finish, so each is written at once and a stopped job loses none."""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fn, it) for it in items]
        for fut in as_completed(futures):
            yield fut.result()


def first_per_type(items: list[dict], n: int | None) -> list[dict]:
    """The first n items of each question type (dataset x template), for smoke tests."""
    if n is None:
        return items
    seen: dict[tuple[str, str], int] = {}
    out = []
    for it in items:
        k = (it["dataset"], it["template_id"])
        if seen.get(k, 0) < n:
            out.append(it)
            seen[k] = seen.get(k, 0) + 1
    return out


# --------------------------------------------------------------------------- generate
def shown(unit: dict) -> str:
    fr = unit["frames"]
    return (f"{len(fr)} frames from {fr[0]['label']} to {fr[-1]['label']} of the recording"
            if fr else "no frames")


def user_content(unit: dict) -> list[dict]:
    fr = unit["frames"]
    parts = [{"type": "text", "text": (
        f"{unit['context']}\n"
        f"Below are {len(fr)} frames from this recording, from {fr[0]['label']} to {fr[-1]['label']}, "
        "in order. Each frame comes right after its time in the recording.\n")}]
    for f in fr:
        data = base64.b64encode(Path(f["path"]).read_bytes()).decode()
        parts.append({"type": "text", "text": f"\n[{f['label']}]\n"})
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}})
    parts.append({"type": "text", "text": f"\n\nQuestion: {unit['question']}"})
    return parts


def generate(unit: dict, args, system: str) -> dict:
    body = {"messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user_content(unit)}],
            "temperature": args.temperature, "top_p": args.top_p, "max_tokens": args.max_tokens,
            "stream": False}
    if args.top_k is not None:
        body["top_k"] = args.top_k
    if args.presence_penalty:
        body["presence_penalty"] = args.presence_penalty
    if args.thinking is not None:
        body["chat_template_kwargs"] = {"enable_thinking": args.thinking}
    if args.served_model:
        body["model"] = args.served_model
    started, errors, reply, answer, tries = time.time(), [], None, None, 0
    for tries in range(1, 4):
        try:
            reply = post(args.host, body)
            answer = strip_think(reply["content"]) or None
            if answer is None:
                raise ValueError(f"empty answer (finish_reason {reply['finish_reason']})")
            break
        except (ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError,
                ConnectionError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    return {"unit_id": unit["unit_id"], "dataset": unit["dataset"], "template_id": unit["template_id"],
            "question": unit["question"], "answer": answer,
            "status": "answered" if answer else "no_answer", "errors": errors, "requests": tries,
            "frames_shown": len(unit["frames"]), "shown": shown(unit),
            "reasoning": reply["reasoning"] if reply else None,
            "finish_reason": reply["finish_reason"] if reply else None,
            "usage": reply["usage"] if reply else None,
            "elapsed_seconds": round(time.time() - started, 1)}


# --------------------------------------------------------------------------- judge
def as_text(value, sep: str = "\n") -> str:
    if isinstance(value, (list, tuple)):
        return sep.join(as_text(v, sep) for v in value)
    if isinstance(value, dict):
        return sep.join(f"{k}: {as_text(v, '; ')}" for k, v in value.items())
    return str(value)


def indent(text, pad: str = "    ") -> str:
    return "\n".join(pad + line if line.strip() else "" for line in as_text(text).strip().splitlines())


def frame_spacing(unit: dict) -> float:
    ts = [f["t"] for f in unit["frames"]]
    return (ts[-1] - ts[0]) / (len(ts) - 1) if len(ts) > 1 else 1.0


def judge_prompt(template: str, unit: dict, gen: dict) -> str:
    gt = unit["ground_truth"]
    block = "\n".join([
        "[GROUND_TRUTH]",
        f"  setting: {gt['setting']}",
        f"  dataset: {unit['dataset']}",
        f"  {as_text(gt['recorded_label'], '; ')}:",
        indent(gt["recorded"]),
        f"  not recorded in this dataset: {as_text(gt['not_recorded'], '; ')}",
        *(["  localization: NOT APPLICABLE (the question itself names the span or unit)"]
          if str(gt.get("localization", "")).upper().startswith("NOT") else []),
        f"  source annotation: {as_text(gt['source_annotation'], '; ')}",
        f"  what the answering model was shown: the question and {shown(unit)}, one frame about "
        f"every {frame_spacing(unit):.1f} s (the frame spacing), each labelled with its time.",
        "",
        "[RECORD]",
        f"  question: {unit['question']}",
        f"  answer: {gen['answer']}",
    ])
    # Replace the template's placeholder block: the LAST line that is exactly [GROUND_TRUTH]
    # before the output format. The instructions mention "[GROUND_TRUTH]" earlier in running
    # text, and splicing at the first mention would delete the whole rubric.
    end = template.index("Return valid JSON only")
    starts = [m.start() for m in re.finditer(r"^\[GROUND_TRUTH\]\s*$", template[:end], flags=re.MULTILINE)]
    if not starts:
        raise ValueError("no [GROUND_TRUTH] placeholder line in the judge template")
    prompt = template[:starts[-1]] + block + "\n\n" + template[end:]
    for anchor in ("label_score (1-5)", "localization_score (1-5)", "scope_score (1-5)"):
        if anchor not in prompt:  # never send a judge prompt that lost its rubric
            raise SystemExit(f"judge prompt lost its rubric ({anchor!r} missing)")
    return prompt


def json_object(text: str) -> dict:
    cleaned = strip_think(text)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    a, b = cleaned.find("{"), cleaned.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(cleaned[a:b + 1])
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")
    return obj


def judge(unit: dict, gen: dict, args, template: str) -> dict:
    out = {"unit_id": unit["unit_id"], "dataset": unit["dataset"], "template_id": unit["template_id"],
           "model_id": gen.get("model_id"), "judge_id": args.judge_id}
    if not gen.get("answer"):
        return {**out, "judged": False, "reason": "no answer to review"}
    prompt = judge_prompt(template, unit, gen)
    body = {"messages": [{"role": "user", "content": prompt}], "temperature": args.judge_temperature,
            "max_tokens": args.max_tokens, "stream": False}
    if args.judge_top_p is not None:
        body["top_p"] = args.judge_top_p
    kwargs = {}
    if args.thinking is not None:
        kwargs["enable_thinking"] = args.thinking
    if args.reasoning_effort:
        kwargs["reasoning_effort"] = args.reasoning_effort  # gpt-oss: low | medium | high
    if kwargs:
        body["chat_template_kwargs"] = kwargs
    if args.served_model:
        body["model"] = args.served_model
    errors, reply = [], None
    for _ in range(3):
        try:
            reply = post(args.host, body)
            obj = json_object(reply["content"])
            scores = {}
            for c in CHECKS:
                v = obj.get(c)
                if v is not None and v not in (1, 2, 3, 4, 5):
                    raise ValueError(f"{c} out of range: {v!r}")
                scores[c] = v
            return {**out, "judged": True, **scores, "uncertain": obj.get("uncertain", []),
                    "evaluator_notes": obj.get("evaluator_notes", ""), "judge_errors": errors}
        except (ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError,
                ConnectionError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    return {**out, "judged": False, "reason": "; ".join(errors),
            "judge_raw": reply["content"] if reply else None}


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["generate", "judge"])
    ap.add_argument("--units", nargs="+", type=Path, required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--served-model", default=None, help="the 'model' field, for vLLM")
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None,
                    help="set chat_template_kwargs.enable_thinking; omit for models without the switch")
    # generate
    ap.add_argument("--out", type=Path)
    ap.add_argument("--model-id")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--top-p", type=float, default=0.8)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--presence-penalty", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=None, help="first N per question type (smoke test)")
    ap.add_argument("--claim-dir", type=Path, default=None,
                    help="jobs given the same claim dir share one list of questions: each question goes "
                         "to the first job that claims it (an empty file named after the unit)")
    ap.add_argument("--answered-elsewhere", nargs="*", type=Path, default=[],
                    help="this model's other generations.jsonl files; units answered there are skipped")
    # judge
    ap.add_argument("--answers", nargs="+", type=Path, help="generations.jsonl files to review")
    ap.add_argument("--judge-id")
    ap.add_argument("--judge-temperature", type=float, default=0.0)
    ap.add_argument("--judge-top-p", type=float, default=None)
    ap.add_argument("--reasoning-effort", default=None, help="for gpt-oss judges: low, medium or high")
    args = ap.parse_args()

    units = load_units(args.units)
    by_id = {u["unit_id"]: u for u in units}

    if args.phase == "generate":
        if not (args.out and args.model_id):
            ap.error("generate needs --out and --model-id")
        args.out.mkdir(parents=True, exist_ok=True)
        system = text_block(SYSTEM_PROMPT)
        gen_path = args.out / "generations.jsonl"
        done = {g["unit_id"] for p in [gen_path, *args.answered_elsewhere]
                for g in read_jsonl(p) if g.get("answer")}
        todo = first_per_type([u for u in units if u["unit_id"] not in done], args.limit)
        settings = {k: getattr(args, k) for k in ("model_id", "temperature", "top_p", "top_k",
                                                  "presence_penalty", "max_tokens", "thinking")}
        (args.out / "settings.json").write_text(json.dumps(
            {**settings, "sampling": units[0]["sampling"] if units else None}, indent=1) + "\n")
        if args.claim_dir:
            args.claim_dir.mkdir(parents=True, exist_ok=True)

        def claim(unit_id: str) -> bool:
            """True if this job takes the unit. Claimed when a worker reaches it, not up front,
            so jobs that start later still find work."""
            if not args.claim_dir:
                return True
            try:
                fd = os.open(args.claim_dir / unit_id, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                return False
            os.write(fd, f"{os.environ.get('SLURM_JOB_ID', 'manual')}\n".encode())
            os.close(fd)
            return True

        def one(u: dict) -> dict | None:
            return generate(u, args, system) if claim(u["unit_id"]) else None

        print(f"generate: {len(todo)} of {len(units)} units with {args.model_id}"
              + (f", shared through {args.claim_dir}" if args.claim_dir else ""), flush=True)
        i = 0
        with gen_path.open("a", encoding="utf-8") as fh:
            for rec in run_pool(one, todo, args.workers):
                if rec is None:  # another job has it
                    continue
                i += 1
                rec["model_id"] = args.model_id
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                u = rec.get("usage") or {}
                print(f"[gen {i}/{len(todo)}] {rec['unit_id']}: {rec['status']} in {rec['elapsed_seconds']}s "
                      f"(prompt {u.get('prompt_tokens')}, out {u.get('completion_tokens')})", flush=True)
        return 0

    if not (args.answers and args.judge_id):
        ap.error("judge needs --answers and --judge-id")
    template = text_block(JUDGE_PROMPT)
    for gen_path in args.answers:
        rev_path = gen_path.with_name(f"reviews-{re.sub(r'[^A-Za-z0-9.-]+', '_', args.judge_id)}.jsonl")
        gens = {}
        for g in read_jsonl(gen_path):  # the last record per unit wins (resumed runs append)
            gens[g["unit_id"]] = g
        done = {r["unit_id"] for r in read_jsonl(rev_path) if r.get("judged")}
        todo = first_per_type([g for k, g in gens.items() if k not in done and k in by_id], args.limit)
        print(f"judge {gen_path.parent.name}: {len(todo)} answers -> {rev_path.name}", flush=True)
        with rev_path.open("a", encoding="utf-8") as fh:
            for i, rec in enumerate(run_pool(lambda g: judge(by_id[g["unit_id"]], g, args, template),
                                             todo, args.workers), 1):
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                s = " ".join(f"{c.split('_')[0]}={rec.get(c)}" for c in CHECKS) if rec.get("judged") \
                    else rec.get("reason")
                print(f"[judge {i}/{len(todo)}] {rec['unit_id']}: {s}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
