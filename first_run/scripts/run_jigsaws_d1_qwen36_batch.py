#!/usr/bin/env python3
"""Generate Template D1 SurgCoach QA pairs for JIGSAWS Suturing in shards.

NOTE: this runner is frame-based. For the current annotation-only phase use
surgical-error-detection/scripts/run_annotation_qa_jigsaws.py. Records are
stamped with model.backend ("real" or "mock"); mock output never goes to the
main output JSONL, only to a sibling *.mock.jsonl file.

The LOW_SCORE_FEEDBACK / label_supported_reference_feedback values below are
reference metadata derived from GRS labels; they are never emitted as the QA
answer (the answer always comes from the model output).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GRS_NAMES = [
    "respect_for_tissue",
    "suture_needle_handling",
    "time_and_motion",
    "flow_of_operation",
    "overall_performance",
    "quality_of_final_product",
]

LOW_SCORE_FEEDBACK = {
    "respect_for_tissue": (
        "tissue handling",
        "Lighten traction on the simulated tissue, use the assisting instrument only to expose the bite, and stop pulling once the needle path is visible.",
    ),
    "suture_needle_handling": (
        "needle control",
        "Set the needle angle for the intended bite, stabilize the needle with the driver before entry, and drive with a controlled wrist rotation instead of pushing or dragging.",
    ),
    "time_and_motion": (
        "economy of motion",
        "Before advancing, choose the next bite point, set the needle angle, drive through in one controlled arc, and avoid extra instrument travel between those steps.",
    ),
    "flow_of_operation": (
        "procedural flow",
        "Stop the extra repositioning, identify the next bite point, align both instruments, and complete that single pass before changing tasks.",
    ),
    "overall_performance": (
        "overall technique",
        "Slow the sequence down, keep both instruments coordinated at the needle, and complete one clean grasp-drive-release cycle before adjusting.",
    ),
    "quality_of_final_product": (
        "final product quality",
        "Aim for equal bite spacing and depth, remove only the slack needed, and check tension before placing the next bite.",
    ),
}

QUESTION_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "Prompts_And_Pipeline"
    / "template-d-question-templates.md"
)
_QUESTION_TEMPLATE_CACHE: dict[str, str] | None = None


def load_question_templates() -> dict[str, str]:
    global _QUESTION_TEMPLATE_CACHE
    if _QUESTION_TEMPLATE_CACHE is not None:
        return _QUESTION_TEMPLATE_CACHE

    text = QUESTION_TEMPLATE_PATH.read_text(encoding="utf-8")
    templates: dict[str, str] = {}
    for match in re.finditer(r"^## Template (D[1-5]): .+\n\n(.+)$", text, re.MULTILINE):
        templates[match.group(1)] = match.group(2).strip()
    missing = sorted(set(f"D{index}" for index in range(1, 6)) - set(templates))
    if missing:
        raise ValueError(f"Missing Template D question wording in {QUESTION_TEMPLATE_PATH}: {missing}")
    _QUESTION_TEMPLATE_CACHE = templates
    return templates


@dataclass(frozen=True)
class TrialMeta:
    trial_id: str
    skill_level: str
    grs_total: int
    grs_subscores: dict[str, int]


@dataclass(frozen=True)
class GestureSpan:
    start_frame: int
    end_frame: int
    gesture_id: str

    @property
    def frame_label(self) -> str:
        return f"{self.start_frame}-{self.end_frame}"


@dataclass(frozen=True)
class Candidate:
    index: int
    trial: TrialMeta
    span: GestureSpan
    capture: str
    candidate_unit: str = "gesture"
    source_span: GestureSpan | None = None

    @property
    def qa_id(self) -> str:
        if self.candidate_unit == "frame":
            return f"jigsaws_suturing_d1_{self.index:06d}_{self.trial.trial_id}_frame_{self.span.start_frame:06d}"
        return f"jigsaws_suturing_d1_{self.index:06d}_{self.trial.trial_id}_{self.span.start_frame}_{self.span.end_frame}"

    @property
    def evidence_label(self) -> str:
        if self.span.start_frame == self.span.end_frame:
            return str(self.span.start_frame)
        return self.span.frame_label

    @property
    def source_gesture_span(self) -> GestureSpan:
        return self.source_span or self.span


def read_meta(dataset_root: Path) -> list[TrialMeta]:
    rows: list[TrialMeta] = []
    for line in (dataset_root / "meta_file_Suturing.txt").read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split()
        scores = {name: int(value) for name, value in zip(GRS_NAMES, parts[3:9])}
        rows.append(TrialMeta(parts[0], parts[1], int(parts[2]), scores))
    return rows


def read_gestures(dataset_root: Path, trial_id: str) -> list[GestureSpan]:
    spans: list[GestureSpan] = []
    path = dataset_root / "transcriptions" / f"{trial_id}.txt"
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        start, end, gesture_id = line.split()[:3]
        spans.append(GestureSpan(int(start), int(end), gesture_id))
    return spans


def probe_video_frame_count(video_path: Path) -> int | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames,nb_frames",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(video_path),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    for line in result.stdout.splitlines():
        value = line.strip()
        if value.isdigit():
            return int(value)
    return None


def build_candidates(
    dataset_root: Path,
    captures: list[str],
    max_examples: int,
    candidate_unit: str,
    frame_stride: int,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    capture_label = ",".join(captures)
    for trial in read_meta(dataset_root):
        video_paths = [dataset_root / "video" / f"{trial.trial_id}_{capture}.avi" for capture in captures]
        if not all(path.exists() for path in video_paths):
            continue
        for span in read_gestures(dataset_root, trial.trial_id):
            usable_span = span
            if candidate_unit == "gesture":
                candidates.append(Candidate(len(candidates), trial, usable_span, capture_label, "gesture", span))
                if max_examples > 0 and len(candidates) >= max_examples:
                    return candidates
                continue
            for frame in range(usable_span.start_frame, usable_span.end_frame + 1, frame_stride):
                frame_span = GestureSpan(frame, frame, span.gesture_id)
                candidates.append(Candidate(len(candidates), trial, frame_span, capture_label, "frame", span))
                if max_examples > 0 and len(candidates) >= max_examples:
                    return candidates
    return candidates


def weakest_subscore(trial: TrialMeta) -> tuple[str, int]:
    return min(trial.grs_subscores.items(), key=lambda item: (item[1], GRS_NAMES.index(item[0])))


def expected_feedback(trial: TrialMeta) -> tuple[str, str]:
    weak_name, _ = weakest_subscore(trial)
    return LOW_SCORE_FEEDBACK[weak_name]


def build_question(candidate: Candidate) -> str:
    frame_text = (
        f"frame {candidate.evidence_label}"
        if candidate.span.start_frame == candidate.span.end_frame
        else f"frames {candidate.evidence_label}"
    )
    return load_question_templates()["D1"].replace("[a certain video span]", frame_text)


def build_prompt(candidate: Candidate) -> str:
    focus, _ = expected_feedback(candidate.trial)
    weak_name, weak_score = weakest_subscore(candidate.trial)
    return f"""You are an expert surgical evaluator, attending physician, and AI surgical copilot. Your objective is to analyze surgical video data, including frames, clips, or sequential metadata, and provide precise, objective assessments aligned with JIGSAWS/Suturing. When the available evidence is insufficient, explicitly state that the observation cannot be determined rather than making speculative conclusions.

Task: Generate exactly one Template D1 coaching-feedback QA pair for a JIGSAWS Suturing simulation clip.

Question template source:
Prompts_And_Pipeline/template-d-question-templates.md

Critical output rule:
The next assistant message must begin with {{ and end with }}. Do not write analysis, notes, markdown, or prose before or after the JSON object.

Execution rules:
1. Output one valid JSON object only. Do not wrap it in markdown.
2. The JSON object must contain exactly these keys: question, answer, rationale.
3. Use annotations. Treat provided annotations, kinematic telemetry, or multimodal tracking data as ground-truth context, but keep the final answer honest about what is visually observable.
4. JIGSAWS provides trial-level skill ratings and gesture frame spans. Trial-level scores can explain the coaching focus for the overall trial, but they do not prove that every individual frame or gesture shows the same error.
5. Every output must anchor on the specific question, provide the requested coaching feedback, and supply a clinical rationale that includes the relevant frame or frame range in prose.
6. Do not invent tissue tearing, injury, bleeding, anatomy, complications, patient-specific risk, or tool actions that are not visible or annotated.
7. If the annotations do not support a reliable answer, write "cannot determine" in the answer and explain the limitation in the rationale.

Field routing:
1. Put only the exact requested question in the `question` field.
2. Put trainee-facing coaching in the `answer` field. Make it detailed, specific, and instructive. It may mention the relevant technical field or domain in prose when useful, but never as a separate JSON field.
3. Put detailed evidence analysis, the exact frame or frame range, and any visual-limitation statement in the `rationale` field.
4. Do not add extra top-level fields.

Generation behavior for `answer`:
1. Do not limit the feedback to a single sentence. Write a detailed, instructive coaching note.
2. Be mechanically specific. Name the visible or annotated movement problem, the next movement the trainee should perform, and how to perform it.
3. If you use planning language, spell out the plan: choose the next bite point or target, set the needle angle, align the instruments, drive or regrasp, then continue.
4. Do not copy canned reference feedback. If a coaching focus such as "economy of motion" or "tissue handling" is provided, use it as a clinical direction, not as a sentence template.
5. Do not mention raw rubric field names, metadata keys, or numeric dataset labels in the answer.

Generation behavior for `rationale`:
1. Explain in detail why the answer is supported using natural clinical language.
2. State the relevant frame or frame range.
3. Describe visible evidence when available, and mention annotation context only when it helps ground the answer.
4. Choose one primary feedback priority for each QA item: patient safety-critical, procedural efficiency, or technique-execution. Prioritize the most clinically relevant one, and mention secondary issues only lightly if they are visible and useful.
5. Combine related dataset fields when grounding the rationale. For example, if the answer is about tissue handling, consider both respect for tissue and suture/needle handling, along with any relevant gesture annotations, rather than relying on a single subscore in isolation.
6. If visual evidence is limited, say that briefly in the rationale while still giving the best supported answer.

Dataset and labels:
- dataset: JIGSAWS/Suturing
- trial_id: {candidate.trial.trial_id}
- capture: {candidate.capture}
- candidate_unit: {candidate.candidate_unit}
- evidence_frame_or_range: {candidate.evidence_label}
- source_gesture_frame_range: {candidate.source_gesture_span.frame_label}
- gesture_id: {candidate.source_gesture_span.gesture_id}
- skill_level: {candidate.trial.skill_level}
- grs_total: {candidate.trial.grs_total}
- grs_subscores: {json.dumps(candidate.trial.grs_subscores, sort_keys=True)}

Label-supported coaching focus:
- weakest_subscore: {weak_name}={weak_score}
- coaching_focus: {focus}

Question:
{build_question(candidate)}

Return this exact JSON shape:
{{
  "question": "{build_question(candidate)}",
  "answer": "field: answer; detailed trainee-facing coaching with concrete movement instructions, or cannot determine",
  "rationale": "field: rationale; detailed evidence analysis that includes the exact frame or frame range, uncertainty, and avoids unsupported conclusions"
}}
"""


_VIDEO_READER_CACHE: dict[str, Any] = {}


def get_video_reader(video_path: Path) -> Any:
    import decord  # type: ignore

    key = str(video_path)
    if key not in _VIDEO_READER_CACHE:
        _VIDEO_READER_CACHE[key] = decord.VideoReader(key)
    return _VIDEO_READER_CACHE[key]


def frame_indices(video_len: int, span: GestureSpan, num_frames: int) -> list[int]:
    if video_len <= 0:
        raise ValueError("Video has no readable frames.")
    start = max(span.start_frame - 1, 0)
    end = min(span.end_frame - 1, video_len - 1)
    if start > end:
        raise ValueError(
            f"Requested span {span.frame_label} is outside readable video length {video_len}."
        )
    count = max(1, min(num_frames, end - start + 1))
    if count == 1:
        return [start]
    step = (end - start) / (count - 1)
    return [int(round(start + step * i)) for i in range(count)]


def evidence_indices(video_len: int, candidate: Candidate, num_frames: int, frame_context_radius: int) -> list[int]:
    if video_len <= 0:
        raise ValueError("Video has no readable frames.")
    if candidate.candidate_unit == "frame":
        target = max(candidate.span.start_frame - 1, 0)
        if target >= video_len:
            raise ValueError(
                f"Requested frame {candidate.span.start_frame} is outside readable video length {video_len}."
            )
        start = max(target - frame_context_radius, 0)
        end = min(target + frame_context_radius, video_len - 1)
        return list(range(start, end + 1))
    return frame_indices(video_len, candidate.span, num_frames)


def extract_evidence_frames(
    video_path: Path,
    candidate: Candidate,
    output_dir: Path,
    num_frames: int,
    frame_context_radius: int,
) -> tuple[list[Path], list[int], int]:
    from PIL import Image

    output_dir.mkdir(parents=True, exist_ok=True)
    reader = get_video_reader(video_path)
    video_len = len(reader)
    indices = evidence_indices(video_len, candidate, num_frames, frame_context_radius)
    batch = reader.get_batch(indices).asnumpy()
    paths: list[Path] = []
    for source_idx, arr in zip(indices, batch):
        frame_path = output_dir / f"{video_path.stem}_frame_{source_idx + 1:06d}.jpg"
        if not frame_path.exists():
            Image.fromarray(arr).save(frame_path, quality=92)
        paths.append(frame_path)
    return paths, [index + 1 for index in indices], video_len


def extract_visual_evidence(
    dataset_root: Path,
    candidate: Candidate,
    captures: list[str],
    output_dir: Path,
    num_frames: int,
    frame_context_radius: int,
) -> dict[str, Any]:
    all_frame_paths: list[str] = []
    videos: list[dict[str, Any]] = []
    expected_indices: list[int] | None = None
    for capture in captures:
        video_path = dataset_root / "video" / f"{candidate.trial.trial_id}_{capture}.avi"
        capture_dir = output_dir / capture
        frame_paths, evidence_indices_1based, video_frame_count = extract_evidence_frames(
            video_path,
            candidate,
            capture_dir,
            num_frames,
            frame_context_radius,
        )
        if expected_indices is None:
            expected_indices = evidence_indices_1based
        elif evidence_indices_1based != expected_indices:
            raise ValueError(f"Capture frame-index mismatch for {candidate.qa_id}: {capture}")
        frame_path_strings = [str(path) for path in frame_paths]
        all_frame_paths.extend(frame_path_strings)
        videos.append(
            {
                "capture": capture,
                "video_path": str(video_path),
                "video_frame_count": video_frame_count,
                "frame_paths": frame_path_strings,
            }
        )

    source_span = candidate.source_gesture_span
    return {
        "evidence_type": "single_frame" if candidate.candidate_unit == "frame" else "frame_sequence",
        "candidate_unit": candidate.candidate_unit,
        "captures": captures,
        "video_id": candidate.trial.trial_id,
        "evidence_frame_or_range": candidate.evidence_label,
        "evidence_frame_indices": expected_indices or [],
        "frame_paths": all_frame_paths,
        "videos": videos,
        "source_gesture": {
            "gesture_id": source_span.gesture_id,
            "start_frame": source_span.start_frame,
            "end_frame": source_span.end_frame,
            "frame_range": source_span.frame_label,
        },
    }


def build_visual_evidence_stub(candidate: Candidate, captures: list[str]) -> dict[str, Any]:
    source_span = candidate.source_gesture_span
    videos = [
        {
            "capture": capture,
            "video_path": None,
            "video_frame_count": None,
            "frame_paths": [],
        }
        for capture in captures
    ]
    return {
        "evidence_type": "single_frame" if candidate.candidate_unit == "frame" else "frame_sequence",
        "candidate_unit": candidate.candidate_unit,
        "captures": captures,
        "video_id": candidate.trial.trial_id,
        "evidence_frame_or_range": candidate.evidence_label,
        "evidence_frame_indices": [],
        "frame_paths": [],
        "videos": videos,
        "source_gesture": {
            "gesture_id": source_span.gesture_id,
            "start_frame": source_span.start_frame,
            "end_frame": source_span.end_frame,
            "frame_range": source_span.frame_label,
        },
    }


def frame_paths_for_captures(visual_evidence: dict[str, Any], captures: list[str]) -> list[Path]:
    selected: list[Path] = []
    wanted = set(captures)
    for video in visual_evidence.get("videos", []):
        if video.get("capture") in wanted:
            selected.extend(Path(path) for path in video.get("frame_paths", []))
    return selected


def load_model(model_id: str, device_map: str) -> tuple[Any, Any]:
    import torch
    from transformers import AutoProcessor
    import transformers

    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    kwargs: dict[str, Any] = {"device_map": device_map, "trust_remote_code": True}
    kwargs["dtype"] = torch.bfloat16 if torch.cuda.is_available() else "auto"

    model_class_names = [
        "AutoModelForMultimodalLM",
        "AutoModelForImageTextToText",
        "AutoModelForVision2Seq",
        "AutoModelForCausalLM",
    ]
    errors: list[str] = []
    for class_name in model_class_names:
        model_cls = getattr(transformers, class_name, None)
        if model_cls is None:
            continue
        try:
            model = model_cls.from_pretrained(model_id, **kwargs)
            model.eval()
            return processor, model
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{class_name}: {exc}")
    raise RuntimeError("Could not load model with any AutoModel class:\n" + "\n".join(errors))


def run_generation(
    processor: Any,
    model: Any,
    prompt: str,
    frame_paths: list[Path],
    max_new_tokens: int,
) -> str:
    import torch
    from qwen_vl_utils import process_vision_info

    system_message = (
        "You are in JSON-only generation mode. Do not reveal reasoning. "
        "Return exactly one compact JSON object and no other text."
    )
    content: list[dict[str, Any]] = [{"type": "image", "image": path.resolve().as_uri()} for path in frame_paths]
    content.append({"type": "text", "text": prompt})
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": content},
    ]
    text = processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=True,
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    inputs = inputs.to(device)
    with torch.no_grad():
        generated_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
        )
    generated_ids_trimmed = [
        out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
    ]
    return processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )[0]


def parse_model_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    stripped = text.strip()
    stripped = re.sub(r"<think>.*?</think>", "", stripped, flags=re.DOTALL | re.IGNORECASE).strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        stripped = fence_match.group(1).strip()
    if not stripped.startswith("{"):
        start = stripped.find("{")
        if start >= 0:
            depth = 0
            in_string = False
            escape = False
            end = None
            for index, char in enumerate(stripped[start:], start=start):
                if escape:
                    escape = False
                    continue
                if char == "\\":
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if end is not None:
                stripped = stripped[start:end]
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return None, f"json_parse_error: {exc}"
    if not isinstance(parsed, dict):
        return None, "output_not_object"
    expected_keys = {"question", "answer", "rationale"}
    if set(parsed) != expected_keys:
        return None, f"schema_key_mismatch: {sorted(parsed)}"
    return parsed, None


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def mock_output_path(output_jsonl: str) -> Path:
    path = Path(output_jsonl)
    return path.with_name(path.stem + ".mock" + path.suffix)


def build_base_record(
    candidate: Candidate,
    visual_evidence: dict[str, Any],
    model_id: str,
    prompt: str,
) -> dict[str, Any]:
    focus, feedback = expected_feedback(candidate.trial)
    weak_name, weak_score = weakest_subscore(candidate.trial)
    source_span = candidate.source_gesture_span
    return {
        "qa_id": candidate.qa_id,
        "dataset": "JIGSAWS",
        "procedure_or_task": "Suturing",
        "template_category": "D",
        "template_id": "D1",
        "question_type": "coaching_feedback",
        "video_id": f"{candidate.trial.trial_id}_{candidate.capture}",
        "timestamp_or_frame": candidate.evidence_label,
        "question": build_question(candidate),
        "label_supported_focus": focus,
        "label_supported_reference_feedback": feedback,
        "source_annotation": {
            "trial_id": candidate.trial.trial_id,
            "capture": candidate.capture,
            "candidate_unit": candidate.candidate_unit,
            "gesture_id": source_span.gesture_id,
            "start_frame": candidate.span.start_frame,
            "end_frame": candidate.span.end_frame,
            "source_gesture_start_frame": source_span.start_frame,
            "source_gesture_end_frame": source_span.end_frame,
            "skill_level": candidate.trial.skill_level,
            "grs_total": candidate.trial.grs_total,
            "weakest_subscore": {"name": weak_name, "score": weak_score},
            "grs_subscores": candidate.trial.grs_subscores,
        },
        "visual_evidence": visual_evidence,
        "model": {
            "model_id": model_id,
            "backend": None,
            "sampled_frames": list(visual_evidence.get("frame_paths", [])),
        },
        "prompt": prompt,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default="/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing")
    parser.add_argument("--capture", default=None, choices=["capture1", "capture2"])
    parser.add_argument("--captures", default="capture1,capture2")
    parser.add_argument("--generation-captures", default="capture1")
    parser.add_argument("--model-id", default="Qwen/Qwen3.6-35B-A3B")
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--rejected-jsonl", required=True)
    parser.add_argument("--frame-output-dir", required=True)
    parser.add_argument("--max-examples", type=int, default=0, help="0 means use the full candidate set.")
    parser.add_argument("--candidate-unit", choices=["frame", "gesture"], default="frame")
    parser.add_argument("--frame-stride", type=int, default=1)
    parser.add_argument("--frame-context-radius", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=2)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--num-frames", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--min-valid", type=int, default=1)
    parser.add_argument("--fail-on-rejected", action="store_true")
    parser.add_argument("--mock-raw-output", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def parse_capture_list(captures_arg: str | None, capture_arg: str | None) -> list[str]:
    raw_captures = captures_arg if captures_arg else capture_arg
    if not raw_captures:
        raw_captures = "capture1,capture2"
    captures = [capture.strip() for capture in raw_captures.split(",") if capture.strip()]
    invalid = [capture for capture in captures if capture not in {"capture1", "capture2"}]
    if invalid:
        raise ValueError(f"Invalid capture values: {invalid}")
    if not captures:
        raise ValueError("At least one capture is required.")
    return captures


def main() -> None:
    args = parse_args()
    if args.shard_index < 0 or args.shard_index >= args.num_shards:
        raise ValueError("--shard-index must be in [0, num_shards)")
    if args.frame_stride < 1:
        raise ValueError("--frame-stride must be >= 1")
    if args.frame_context_radius < 0:
        raise ValueError("--frame-context-radius must be >= 0")

    dataset_root = Path(args.dataset_root)
    captures = parse_capture_list(args.captures, args.capture)
    generation_captures = parse_capture_list(args.generation_captures, None)
    missing_generation_captures = [capture for capture in generation_captures if capture not in captures]
    if missing_generation_captures:
        raise ValueError(f"--generation-captures must be a subset of --captures: {missing_generation_captures}")
    candidates = build_candidates(
        dataset_root,
        captures,
        args.max_examples,
        args.candidate_unit,
        args.frame_stride,
    )
    shard_candidates = [
        candidate
        for position, candidate in enumerate(candidates)
        if position % args.num_shards == args.shard_index
    ]
    print(
        json.dumps(
            {
                "model_id": args.model_id,
                "total_candidates": len(candidates),
                "candidate_unit": args.candidate_unit,
                "captures": captures,
                "generation_captures": generation_captures,
                "frame_stride": args.frame_stride,
                "frame_context_radius": args.frame_context_radius,
                "shard_index": args.shard_index,
                "num_shards": args.num_shards,
                "shard_candidates": len(shard_candidates),
                "dry_run": args.dry_run,
            },
            indent=2,
        ),
        flush=True,
    )

    processor = model = None
    if not args.dry_run and args.mock_raw_output is None:
        processor, model = load_model(args.model_id, args.device_map)

    valid_count = 0
    rejected_count = 0
    dry_run_count = 0
    for candidate in shard_candidates:
        frame_dir = Path(args.frame_output_dir) / candidate.qa_id
        prompt = build_prompt(candidate)
        visual_evidence = build_visual_evidence_stub(candidate, captures)
        record = build_base_record(
            candidate,
            visual_evidence,
            args.model_id,
            prompt,
        )
        record["model"]["generation_captures"] = generation_captures
        record["model"]["generation_frame_paths"] = []

        try:
            if args.dry_run:
                if args.mock_raw_output is None:
                    record["model"]["raw_output"] = None
                    record["parsed_output"] = None
                    record["validation_status"] = "dry_run"
                    dry_run_count += 1
                    continue

                parsed_output, error = parse_model_json(args.mock_raw_output)
                if error is None and parsed_output is not None:
                    if parsed_output.get("question") != build_question(candidate):
                        error = "question_mismatch"
                record["model"]["backend"] = "mock"
                record["model"]["raw_output"] = args.mock_raw_output
                record["parsed_output"] = parsed_output
                record["validation_status"] = "valid_mock" if error is None else "rejected"
                record["validation_error"] = error
                if error is None:
                    valid_count += 1
                    append_jsonl(mock_output_path(args.output_jsonl), record)
                else:
                    rejected_count += 1
                    append_jsonl(Path(args.rejected_jsonl), record)
                continue
            visual_evidence = extract_visual_evidence(
                dataset_root,
                candidate,
                captures,
                frame_dir,
                args.num_frames,
                args.frame_context_radius,
            )
            record = build_base_record(
                candidate,
                visual_evidence,
                args.model_id,
                prompt,
            )
            generation_frame_paths = frame_paths_for_captures(visual_evidence, generation_captures)
            if not generation_frame_paths:
                raise ValueError(f"No generation frame paths for {candidate.qa_id}")
            record["model"]["generation_captures"] = generation_captures
            record["model"]["generation_frame_paths"] = [str(path) for path in generation_frame_paths]
            backend = "mock" if args.mock_raw_output is not None else "real"
            if args.mock_raw_output is not None:
                raw_output = args.mock_raw_output
            else:
                raw_output = run_generation(processor, model, prompt, generation_frame_paths, args.max_new_tokens)
            parsed_output, error = parse_model_json(raw_output)
            if error is None and parsed_output is not None:
                if parsed_output.get("question") != build_question(candidate):
                    error = "question_mismatch"
            record["model"]["backend"] = backend
            record["model"]["raw_output"] = raw_output
            record["parsed_output"] = parsed_output
            if error is None:
                record["validation_status"] = "valid" if backend == "real" else "valid_mock"
            else:
                record["validation_status"] = "rejected"
            record["validation_error"] = error
            if error is None:
                valid_count += 1
                if backend == "mock":
                    append_jsonl(mock_output_path(args.output_jsonl), record)
                else:
                    append_jsonl(Path(args.output_jsonl), parsed_output)
            else:
                rejected_count += 1
                append_jsonl(Path(args.rejected_jsonl), record)
        except Exception as exc:  # noqa: BLE001
            record.setdefault("model", {})
            record["model"].setdefault("model_id", args.model_id)
            record["model"].setdefault("prompt", prompt)
            record["model"].setdefault("sampled_frames", [])
            record["model"].setdefault("generation_captures", generation_captures)
            record["model"].setdefault("generation_frame_paths", [])
            record["model"]["backend"] = "mock" if args.mock_raw_output is not None else "real"
            record["model"]["raw_output"] = None
            record["parsed_output"] = None
            record["validation_status"] = "error"
            record["validation_error"] = repr(exc)
            append_jsonl(Path(args.rejected_jsonl), record)
            raise

    summary = {
        "valid_count": valid_count,
        "rejected_count": rejected_count,
        "dry_run_count": dry_run_count,
        "processed_count": valid_count + rejected_count + dry_run_count,
        "min_valid": args.min_valid,
        "fail_on_rejected": args.fail_on_rejected,
    }
    print(json.dumps(summary, indent=2), flush=True)
    success_count = dry_run_count + valid_count if args.dry_run else valid_count
    if success_count < args.min_valid:
        row_kind = "dry-run rows" if args.dry_run else "valid rows"
        print(f"Expected at least {args.min_valid} {row_kind}, got {success_count}.", file=sys.stderr)
        raise SystemExit(3)
    if args.fail_on_rejected and rejected_count:
        print(f"Rejected rows present: {rejected_count}.", file=sys.stderr)
        raise SystemExit(4)


if __name__ == "__main__":
    main()
