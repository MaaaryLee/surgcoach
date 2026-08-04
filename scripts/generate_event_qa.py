#!/usr/bin/env python3
"""Generate event-grounded QA: one question per localized moment, not per trial.

Written against review feedback that the Type C/D answers were generic. The cause
was measured rather than guessed: 7 of our 10 templates ask a literally identical
question for every trial, differing only in the timestamp, so nothing in the input
distinguishes one moment from another and nothing in the output can.

Every question here carries the trial's own numbers, which means the question text
differs per trial and the answer has something specific to address. It also means
the answer is checkable: the fact is stated in the question and drawn from
annotations, so a reviewer can verify it without watching the video.

Question kinds, all built from scripts/localize_events.py output plus the
transcription:

  outlier      a span that took far longer than the same gesture takes in the
               strongest trials
  wandering    a span where the instrument travelled much further than it does in
               the strongest trials
  repetition   a gesture the trial returned to several separate times
  economy      total gesture-segment count against the task median, answered as
               above / at / below (a closed choice, which C7 showed verifies best)
  clean        for a span that beat the comparison, so the set is not only faults

Two inference backends, because the two machines this runs on differ. Locally it
talks to Ollama over HTTP against a 4-bit quantisation; on the cluster there is no
Ollama and the model is loaded in-process at full precision through transformers.
The questions, the prompt and the record schema are identical either way, so the
only variable between the two is the model itself.

    python3 scripts/generate_event_qa.py --task Knot_Tying --trial Knot_Tying_G004
    python3 scripts/generate_event_qa.py --trials Suturing_C004,Knot_Tying_G004 -o outputs/event_qa
    python3 scripts/generate_event_qa.py --task Suturing --backend transformers
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from localize_events import (FPS, GESTURES, build_baselines, events_for_trial,
                             read_meta, read_spans, reference_trials,
                             resolve_task_root, timestamp)
from run_annotation_qa_jigsaws import (TRANSPORT_BACKOFF_SECONDS, TRANSPORT_RETRIES,
                                       extract_system_prompt, load_text_model,
                                       parse_json_payload, run_generation)

USER_PROMPT = (
    "Answer this question about one moment of a bench-top surgical training "
    "recording. No frames are attached; the question states everything measured.\n\n"
    "Question (copy verbatim into the question field):\n{question}\n\n"
    "Return valid JSON only, following the system prompt schema.")

TASK_OF = {"Suturing": "Suturing", "Knot_Tying": "Knot_Tying", "Needle_Passing": "Needle_Passing"}


def reference_peers(meta: dict, trial: str) -> list[str]:
    """The comparison group: strongest trials by score, excluding this one."""
    return reference_trials(meta, exclude=trial)


def build_questions(task: str, trial: str, root: Path, meta: dict,
                    events: list[dict]) -> list[dict[str, Any]]:
    """One question per notable moment, each stating the fact it rests on."""
    spans = read_spans(root, trial)
    if not spans:
        return []
    out: list[dict[str, Any]] = []

    # --- localized duration outliers, worst first
    for ev in sorted([e for e in events if e["kind"] == "slow"],
                     key=lambda e: -e["evidence"]["ratio"])[:2]:
        d = ev["evidence"]
        out.append({
            "kind": "outlier",
            "span": ev["span"],
            "question": (
                f"Between {ev['span']} of this recording the trainee spent "
                f"{d['duration_s']:.1f} seconds on {ev['gesture_label']}, where the "
                f"strongest recorded attempts at that same action take about "
                f"{d['expert_median_s']:.1f} seconds. What most likely caused the delay, "
                f"and what should the trainee do differently on the next repetition?"),
            "grounding": d,
        })

    # --- excess instrument travel
    for ev in sorted([e for e in events if e["kind"] == "wandering"],
                     key=lambda e: -e["evidence"]["ratio"])[:1]:
        d = ev["evidence"]
        out.append({
            "kind": "wandering",
            "span": ev["span"],
            "question": (
                f"Between {ev['span']}, while {ev['gesture_label']}, the trainee's "
                f"{d['side']} instrument travelled {d['ratio']:.1f} times further than it "
                f"does in the strongest recorded attempts at that action. What does that "
                f"extra travel suggest, and what single change would tighten it?"),
            "grounding": d,
        })

    # --- a gesture repeated more often than the task itself requires
    #
    # Not a raw count. Suturing_C004 scores 30 of 30 and still "positions the
    # needle" four times, because a four-stitch task needs four positionings --
    # asking about that measures the task's structure, not the trainee. So the
    # count is compared against the median count for the same gesture in the
    # strongest trials, and only a genuine excess becomes a question.
    counts = Counter(g for _, _, g in spans)
    peer_counts: dict[str, list[int]] = {}
    for other in reference_peers(meta, trial):
        other_spans = read_spans(root, other)
        if not other_spans:
            continue
        oc = Counter(g for _, _, g in other_spans)
        for g in counts:
            peer_counts.setdefault(g, []).append(oc.get(g, 0))
    excess = []
    for gesture, n in counts.items():
        peers_g = peer_counts.get(gesture, [])
        if len(peers_g) < 4:
            continue
        med = statistics.median(peers_g)
        if med >= 1 and n >= med + 2 and n >= 1.5 * med:
            excess.append((n - med, gesture, n, med))
    if excess:
        _, gesture, n, med = max(excess)
        occurrences = [timestamp(s) for s, _, g in spans if g == gesture]
        out.append({
            "kind": "repetition",
            "span": f"{timestamp(spans[0][0])}-{timestamp(spans[-1][1])}",
            "question": (
                f"During this recording the trainee returned to {GESTURES.get(gesture, gesture)} "
                f"{n} separate times, starting at {', '.join(occurrences[:4])}, where the "
                f"strongest recorded attempts at this task need it about {med:.0f} times. "
                f"What does needing the extra attempts indicate, and what one change would "
                f"reduce them?"),
            "grounding": {"gesture": gesture, "occurrences": n,
                          "peer_median": med, "starts": occurrences},
        })

    # --- closed choice on motion economy, the C7 pattern
    peers = [len(read_spans(root, t)) for t in meta if t != trial and read_spans(root, t)]
    if peers:
        median = statistics.median(peers)
        out.append({
            "kind": "economy",
            "span": f"{timestamp(spans[0][0])}-{timestamp(spans[-1][1])}",
            "question": (
                f"Completing this task took the trainee {len(spans)} separate gesture "
                f"segments, where the median across other recorded attempts at this task is "
                f"{median:.0f}. Is their economy of motion above, at, or below the typical "
                f"level for this task? Answer with one of those three, then give one "
                f"sentence of justification."),
            "grounding": {"segments": len(spans), "task_median": median},
        })

    # --- something done well, so the set is not only faults
    good = sorted([e for e in events if e["kind"] == "efficient"],
                  key=lambda e: e["evidence"]["ratio"])[:1]
    for ev in good:
        d = ev["evidence"]
        out.append({
            "kind": "clean",
            "span": ev["span"],
            "question": (
                f"Between {ev['span']} the trainee completed {ev['gesture_label']} in "
                f"{d['duration_s']:.1f} seconds, faster than the {d['expert_median_s']:.1f} "
                f"seconds the strongest recorded attempts take. What does that suggest they "
                f"have established, and what should they be careful to preserve as the task "
                f"gets harder?"),
            "grounding": d,
        })
    return out


def generate_ollama(host: str, model: str, system_prompt: str, question: str,
                    num_ctx: int, max_new_tokens: int, temperature: float) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": USER_PROMPT.format(question=question)},
        ],
        "stream": False,
        "think": True,
        "options": {"temperature": temperature, "num_predict": max_new_tokens, "num_ctx": num_ctx},
    }
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    for attempt in range(TRANSPORT_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                result = json.loads(response.read())
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == TRANSPORT_RETRIES - 1:
                raise
            wait = TRANSPORT_BACKOFF_SECONDS * (attempt + 1)
            print(f"    transport error ({exc}); retrying in {wait}s")
            import time
            time.sleep(wait)
    msg = result["message"]
    thinking, content = msg.get("thinking") or "", msg.get("content") or ""
    return f"<think>{thinking}</think>{content}" if thinking else content


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jigsaws-root", default="outputs/datasets/JIGSAWS")
    ap.add_argument("--system-prompt", default="Prompts_And_Pipeline/system-prompt-events.md")
    ap.add_argument("--trials", default=None, help="comma separated, e.g. Suturing_C004,Knot_Tying_G004")
    ap.add_argument("--task", default=None)
    ap.add_argument("--trial", default=None)
    ap.add_argument("--backend", choices=["ollama", "transformers"], default="ollama",
                    help="ollama over HTTP (local, quantised) or transformers "
                         "in-process (cluster, full precision)")
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--ollama-model", default="qwen3.6-35b-a3b-iq4xs")
    ap.add_argument("--ollama-num-ctx", type=int, default=12288)
    ap.add_argument("--model-id", default="Qwen/Qwen3.6-35B-A3B",
                    help="transformers backend only")
    ap.add_argument("--device-map", default="auto", help="transformers backend only")
    ap.add_argument("--max-new-tokens", type=int, default=9000)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--questions-only", action="store_true",
                    help="print the questions and exit, without calling the model")
    ap.add_argument("--resume", action="store_true",
                    help="append to an existing qa_records.jsonl, skipping every "
                         "qa_id already recorded valid; for restarting a killed job")
    ap.add_argument("-o", "--output-dir", default="outputs/event_qa")
    args = ap.parse_args()

    wanted = []
    if args.trials:
        wanted = [t.strip() for t in args.trials.split(",") if t.strip()]
    elif args.trial:
        wanted = [args.trial]
    elif args.task:
        root = resolve_task_root(Path(args.jigsaws_root), args.task)
        wanted = [t for t in read_meta(root, args.task)
                  if (root / "transcriptions" / f"{t}.txt").exists()]
    else:
        ap.error("give --trial, --trials or --task")

    system_prompt = extract_system_prompt(Path(args.system_prompt))
    print(f"system prompt: {len(system_prompt)} chars")

    # Resolve every trial's questions before loading a 69 GB model, so a bad
    # --jigsaws-root or a missing kinematics directory fails in seconds rather
    # than after the load. Nothing here calls the model.
    plan: list[tuple[str, str, dict, list[dict[str, Any]]]] = []
    for trial in wanted:
        task = next((t for t in TASK_OF if trial.startswith(t)), None)
        if not task:
            print(f"  {trial}: cannot infer task from the trial id, skipping")
            continue
        root = resolve_task_root(Path(args.jigsaws_root), task)
        meta = read_meta(root, task)
        baselines = build_baselines(root, task, meta, exclude=trial)
        events = events_for_trial(root, task, trial, meta, baselines)
        questions = build_questions(task, trial, root, meta, events)
        info = meta.get(trial, {})
        print(f"{trial}  skill {info.get('skill_level')}  GRS {info.get('grs_total')}/30  "
              f"-> {len(questions)} questions")
        plan.append((task, trial, info, questions))
    total_questions = sum(len(q) for *_, q in plan)
    print(f"\n{len(plan)} trials, {total_questions} questions")

    model_label = args.ollama_model if args.backend == "ollama" else args.model_id
    processor = model = None
    if args.backend == "transformers" and not args.questions_only:
        processor, model = load_text_model(args.model_id, args.device_map)

    # Append and flush per record rather than buffering to the end. A cluster run
    # over all of JIGSAWS takes hours, and the earlier all-at-once write meant a
    # preemption discarded everything; it also made a running job look empty,
    # which was misdiagnosed once as the job having been killed.
    out_path = Path(args.output_dir) / "qa_records.jsonl"
    done: set[str] = set()
    if args.resume and out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("validation_status") == "valid":
                    done.add(rec["qa_id"])
        print(f"resuming: {len(done)} valid records already in {out_path}")
    if not args.questions_only:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    sink = out_path.open("a" if args.resume else "w", encoding="utf-8") \
        if not args.questions_only else None

    records: list[dict[str, Any]] = []
    for task, trial, info, questions in plan:
        print(f"\n{trial}  {len(questions)} questions")
        for q in questions:
            if args.questions_only:
                print(f"  [{q['kind']}] {q['question']}")
                continue
            qa_id = f"event_{trial}_{q['kind']}_{q['span'].replace(':', '')}"
            if qa_id in done:
                print(f"  [{q['kind']:10s}] already done, skipping")
                continue
            attempts, errors = 0, []
            qa = None
            raw = ""
            while attempts <= args.max_retries:
                attempts += 1
                if args.backend == "ollama":
                    raw = generate_ollama(args.ollama_host, args.ollama_model, system_prompt,
                                          q["question"], args.ollama_num_ctx,
                                          args.max_new_tokens, args.temperature)
                else:
                    raw = run_generation(processor, model, system_prompt,
                                         USER_PROMPT.format(question=q["question"]),
                                         args.max_new_tokens, args.temperature)
                try:
                    parsed = parse_json_payload(raw)
                    # The prompt asks for a single-element array, and the model
                    # sometimes returns the bare object instead. Rejecting that
                    # discarded 2 of 19 records whose content was complete and
                    # correct, after four attempts each. The wrapper is not the
                    # data, so accept either shape.
                    if isinstance(parsed, dict):
                        parsed = [parsed]
                    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                        qa = parsed[0]
                        break
                    errors.append(f"schema_mismatch: got {type(parsed).__name__}")
                except Exception as exc:  # noqa: BLE001 - record and retry
                    errors.append(f"{type(exc).__name__}: {exc}")
                if attempts > args.max_retries:
                    break
            status = "valid" if qa else "rejected"
            print(f"  [{q['kind']:10s}] {status}"
                  + (f"  ({len(str(qa.get('answer', '')).split())} words)" if qa else ""))
            record = {
                "qa_id": qa_id,
                "dataset": "JIGSAWS", "task": task, "trial_id": trial,
                "question_kind": q["kind"], "span": q["span"],
                "generation_mode": "annotation_only_event_grounded",
                "grounding": q["grounding"],
                "source_annotation": {"skill_level": info.get("skill_level"),
                                      "grs_total": info.get("grs_total")},
                "qa": [qa] if qa else None,
                "validation_status": status,
                "validation_error": errors[-1] if errors and not qa else None,
                "model": {"model_id": model_label, "backend": args.backend,
                          "attempts": attempts,
                          "raw_output": raw if not qa else None,
                          "system_prompt_path": args.system_prompt},
            }
            records.append(record)
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
            sink.flush()

    if sink:
        sink.close()
        valid = sum(1 for r in records if r["validation_status"] == "valid")
        print(f"\nwrote {out_path}: {len(records)} new records, {valid} valid"
              + (f" (+{len(done)} kept from an earlier run)" if done else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
