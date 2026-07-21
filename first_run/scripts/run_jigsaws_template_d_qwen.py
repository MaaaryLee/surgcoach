#!/usr/bin/env python3
"""Run a first Template D coaching QA pass on JIGSAWS Suturing.

DEPRECATED - DO NOT USE FOR DATASET GENERATION.
This script writes label-templated canned text (build_label_supported_answer /
LOW_SCORE_FEEDBACK) into the record's top-level `answer` field, so its records
look like model-generated QA but are not (the actual model output lives only
under `model.output` and can even contradict the canned answer). For the
current annotation-only pipeline use
surgical-error-detection/scripts/run_annotation_qa_jigsaws.py, where every
answer comes from the model and provenance is stamped on each record.

The runner supports two modes:
- dry run: builds the prompt and label-supported expected record without loading Qwen.
- inference: samples frames from the gesture span and sends them to Qwen2.5-VL.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
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

QUESTION_TYPES = {
    "D1": ("coaching_feedback", "one_sentence"),
    "D3": ("corrective_action", "corrective_action"),
    "D4": ("practice_recommendation", "practice_recommendation"),
    "D5": ("positive_reinforcement", "positive_reinforcement"),
}

RESPONSE_FORMATS = {
    "component_structured",
    "taxonomy_structured",
}

TAXONOMY_VERSION = "jigsaws_template_d_v0.1"

TAXONOMY_BY_SUBSCORE = {
    "respect_for_tissue": {
        "error_type": "rough_tissue_handling_label_supported",
        "next_step_category": "gentler_traction",
        "coaching_feedback_category": "tissue_handling",
    },
    "suture_needle_handling": {
        "error_type": "unstable_needle_control_label_supported",
        "next_step_category": "stabilize_needle_angle",
        "coaching_feedback_category": "needle_control",
    },
    "time_and_motion": {
        "error_type": "inefficient_motion_label_supported",
        "next_step_category": "reduce_extra_motion",
        "coaching_feedback_category": "economy_of_motion",
    },
    "flow_of_operation": {
        "error_type": "flow_disruption_label_supported",
        "next_step_category": "pause_and_reorient",
        "coaching_feedback_category": "procedural_flow",
    },
    "overall_performance": {
        "error_type": "general_technique_weakness_label_supported",
        "next_step_category": "controlled_bimanual_movement",
        "coaching_feedback_category": "overall_technique",
    },
    "quality_of_final_product": {
        "error_type": "inconsistent_final_product_label_supported",
        "next_step_category": "consistent_spacing_depth_tension",
        "coaching_feedback_category": "final_product_quality",
    },
}

LOW_SCORE_FEEDBACK = {
    "respect_for_tissue": (
        "tissue handling",
        "Use gentler traction and avoid unnecessary force.",
        "technique",
        "Gentle tissue-handling drill with controlled traction",
    ),
    "suture_needle_handling": (
        "needle handling",
        "Stabilize the needle angle before driving through the target.",
        "technique",
        "Needle angle control drill",
    ),
    "time_and_motion": (
        "economy of motion",
        "Reduce extra instrument travel and plan the next movement before advancing.",
        "efficiency",
        "Slow-motion needle-driving drill with minimal extra instrument travel",
    ),
    "flow_of_operation": (
        "flow of operation",
        "Pause, reorient, and complete one step cleanly before repositioning.",
        "efficiency",
        "Segmented suturing flow drill",
    ),
    "overall_performance": (
        "overall technique",
        "Focus on controlled bimanual movement and consistent needle handling.",
        "technique",
        "Bimanual coordination drill",
    ),
    "quality_of_final_product": (
        "final stitch quality",
        "Practice consistent spacing, depth, and tension across the stitch.",
        "technique",
        "Consistent stitch spacing and tension drill",
    ),
}

HIGH_SCORE_FEEDBACK = {
    "respect_for_tissue": "controlled tissue interaction",
    "suture_needle_handling": "stable needle angle and controlled driving",
    "time_and_motion": "efficient instrument movement",
    "flow_of_operation": "smooth task progression",
    "overall_performance": "consistent overall suturing technique",
    "quality_of_final_product": "consistent final stitch quality",
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


def evidence_span(span: GestureSpan) -> dict[str, Any]:
    if span.start_frame is None or span.end_frame is None:
        return {
            "type": "trial_level",
            "start_frame": None,
            "end_frame": None,
            "gesture_id": span.gesture_id,
            "evidence_source": ["GRS_subscores"],
            "evidence_status": "label_supported",
        }
    return {
        "type": "frame_range",
        "start_frame": span.start_frame,
        "end_frame": span.end_frame,
        "gesture_id": span.gesture_id,
        "evidence_source": ["gesture_transcription", "sampled_frames", "GRS_subscores"],
        "evidence_status": "partially_supported",
    }


def response_scoring_rubric(response_format: str) -> dict[str, Any]:
    if response_format == "taxonomy_structured":
        return {
            "taxonomy_label": {
                "0": "Incorrect or unsupported taxonomy label.",
                "1": "Partially correct broad category but wrong or missing fine-grained label.",
                "2": "Correct label and aligned with annotation or visible evidence.",
            },
            "rationale": {
                "0": "No rationale or contradicts evidence.",
                "1": "Generic rationale with incomplete grounding.",
                "2": "One concise rationale tied to labels and/or visible evidence.",
            },
            "evidence_span": {
                "0": "No frame, timestamp, or evidence source.",
                "1": "Mentions a broad clip/trial but not the requested span.",
                "2": "Provides the correct frame range or states that only trial-level evidence is available.",
            },
        }
    return {
        "best_next_step": {
            "0": "Unsafe, irrelevant, or unsupported next step.",
            "1": "Reasonable but too generic or weakly tied to the labels/evidence.",
            "2": "Specific, action-oriented, and aligned with the label-supported coaching target.",
        },
        "one_sentence_rationale": {
            "0": "Missing, multi-sentence, or contradicts the evidence.",
            "1": "One sentence but generic or only partially grounded.",
            "2": "One sentence tied to the GRS label and/or visible evidence.",
        },
        "supporting_evidence_span": {
            "0": "Missing span/timestamp/evidence source.",
            "1": "Broad or incomplete span.",
            "2": "Correct frame range/timestamp or a clear statement that evidence is trial-level only.",
        },
    }


@dataclass
class TrialMeta:
    trial_id: str
    skill_level: str
    grs_total: int
    grs_subscores: dict[str, int]


@dataclass
class GestureSpan:
    start_frame: int | None
    end_frame: int | None
    gesture_id: str | None

    @property
    def frame_label(self) -> str:
        if self.start_frame is None or self.end_frame is None:
            return "trial_level"
        return f"{self.start_frame}-{self.end_frame}"


def read_meta(dataset_root: Path) -> dict[str, TrialMeta]:
    path = dataset_root / "meta_file_Suturing.txt"
    trials: dict[str, TrialMeta] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split()
        trial_id, skill_level, grs_total = parts[0], parts[1], int(parts[2])
        scores = {name: int(value) for name, value in zip(GRS_NAMES, parts[3:9])}
        trials[trial_id] = TrialMeta(trial_id, skill_level, grs_total, scores)
    return trials


def read_gestures(dataset_root: Path, trial_id: str) -> list[GestureSpan]:
    path = dataset_root / "transcriptions" / f"{trial_id}.txt"
    spans: list[GestureSpan] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        start, end, gesture_id = line.split()[:3]
        spans.append(GestureSpan(int(start), int(end), gesture_id))
    return spans


def choose_span(spans: list[GestureSpan], frame_range: str | None, gesture_id: str | None) -> GestureSpan:
    if frame_range:
        start_s, end_s = frame_range.replace(":", "-").split("-", 1)
        start, end = int(start_s), int(end_s)
        for span in spans:
            if span.start_frame == start and span.end_frame == end:
                return span
        return GestureSpan(start, end, gesture_id)
    if gesture_id:
        for span in spans:
            if span.gesture_id == gesture_id:
                return span
    for preferred in ("G8", "G2", "G3", "G6"):
        for span in spans:
            if span.gesture_id == preferred:
                return span
    return spans[0]


def weakest_subscore(meta: TrialMeta) -> tuple[str, int]:
    return min(meta.grs_subscores.items(), key=lambda item: (item[1], GRS_NAMES.index(item[0])))


def strongest_subscore(meta: TrialMeta) -> tuple[str, int]:
    return max(meta.grs_subscores.items(), key=lambda item: (item[1], -GRS_NAMES.index(item[0])))


def build_label_supported_answer(template_id: str, meta: TrialMeta) -> tuple[str, str, str, str | None]:
    weak_name, weak_score = weakest_subscore(meta)
    focus, message, priority, drill = LOW_SCORE_FEEDBACK[weak_name]

    if template_id == "D5":
        strong_name, strong_score = strongest_subscore(meta)
        positive = HIGH_SCORE_FEEDBACK[strong_name]
        answer = (
            f"Reinforce {positive}; this trial has a {strong_name.replace('_', ' ')} "
            f"subscore of {strong_score}, so the label-supported feedback is positive."
        )
        rationale = (
            f"The selected trial has GRS total {meta.grs_total} and the strongest available "
            f"subscore is {strong_name}={strong_score}."
        )
        return answer, rationale, "positive", None

    if template_id == "D4":
        answer = f"The trainee should practice: {drill}."
        rationale = (
            f"The weakest GRS subscore is {weak_name}={weak_score}, so the recommended drill "
            f"targets {focus}."
        )
        return answer, rationale, priority, drill

    if template_id == "D3":
        answer = message
        rationale = (
            f"The weakest GRS subscore is {weak_name}={weak_score}; this supports a conservative "
            f"corrective action focused on {focus}, pending visual confirmation."
        )
        return answer, rationale, priority, None

    answer = message
    rationale = (
        f"The coaching target is {focus} because the weakest GRS subscore is "
        f"{weak_name}={weak_score}. The exact visual evidence should be verified from the sampled frames."
    )
    return answer, rationale, priority, None


def component_rationale(meta: TrialMeta, span: GestureSpan) -> str:
    weak_name, weak_score = weakest_subscore(meta)
    focus = LOW_SCORE_FEEDBACK[weak_name][0]
    span_text = f"frames {span.frame_label}" if span.frame_label != "trial_level" else "the trial"
    return (
        f"The label-supported target is {focus} because {weak_name} is the weakest GRS "
        f"subscore ({weak_score}/5), with {span_text} used as the visual evidence span."
    )


def taxonomy_labels(template_id: str, meta: TrialMeta) -> dict[str, str]:
    if template_id == "D5":
        strong_name, _ = strongest_subscore(meta)
        return {
            "anatomy": "suturing_pad_or_simulated_tissue",
            "error_type": "none_visible_or_not_requested",
            "next_operative_step": "continue_current_effective_technique",
            "complication": "none_visible_or_not_annotated",
            "coaching_feedback_category": f"positive_reinforcement_{strong_name}",
        }

    weak_name, _ = weakest_subscore(meta)
    labels = TAXONOMY_BY_SUBSCORE[weak_name]
    return {
        "anatomy": "suturing_pad_or_simulated_tissue",
        "error_type": labels["error_type"],
        "next_operative_step": labels["next_step_category"],
        "complication": "none_visible_or_not_annotated",
        "coaching_feedback_category": labels["coaching_feedback_category"],
    }


def build_expected_response(
    response_format: str,
    template_id: str,
    meta: TrialMeta,
    span: GestureSpan,
) -> dict[str, Any]:
    answer, rationale, _, _ = build_label_supported_answer(template_id, meta)
    evidence = evidence_span(span)
    if response_format == "taxonomy_structured":
        return {
            "taxonomy_version": TAXONOMY_VERSION,
            "taxonomy_labels": taxonomy_labels(template_id, meta),
            "short_explanation": component_rationale(meta, span),
            "supporting_evidence_span": evidence,
            "uncertainty": (
                "The taxonomy label is label-supported; visual confirmation is still needed "
                "when the frame evidence is ambiguous."
            ),
        }
    return {
        "best_next_step": answer,
        "one_sentence_rationale": component_rationale(meta, span),
        "supporting_evidence_span": evidence,
        "uncertainty": (
            "The GRS subscore is trial-level, so the frame span should be used to verify "
            "whether the target is visibly supported."
        ),
        "label_supported_rationale": rationale,
    }


def build_question(template_id: str, trial_id: str, span: GestureSpan) -> str:
    location = f"frames {span.frame_label}" if span.frame_label != "trial_level" else "this trial"
    template = load_question_templates()[template_id]
    return template.replace("[a certain video span]", location)


def build_record(
    *,
    dataset_root: Path,
    trial_id: str,
    capture: str,
    template_id: str,
    response_format: str,
    span: GestureSpan,
    meta: TrialMeta,
    model_id: str,
    model_output: str | None = None,
    sampled_frames: list[str] | None = None,
) -> dict[str, Any]:
    question_type, feedback_type = QUESTION_TYPES[template_id]
    answer, rationale, priority, drill = build_label_supported_answer(template_id, meta)
    question = build_question(template_id, trial_id, span)
    frame_token = span.frame_label.replace("-", "_")
    video_id = f"{trial_id}_{capture}"
    qa_id = f"jigsaws_suturing_d_{trial_id}_{template_id}_{frame_token}"
    source_labels = [
        f"skill_level:{meta.skill_level}",
        f"GRS_total:{meta.grs_total}",
    ]
    source_labels.extend(f"GRS_{name}:{score}" for name, score in meta.grs_subscores.items())
    if span.gesture_id:
        source_labels.append(f"gesture_id:{span.gesture_id}")
    if span.frame_label != "trial_level":
        source_labels.append(f"gesture_frame_range:{span.frame_label}")

    return {
        "qa_id": qa_id,
        "dataset": "JIGSAWS",
        "procedure_or_task": "Suturing",
        "video_id": video_id,
        "clip_id": f"{trial_id}_{frame_token}",
        "frame_id": None,
        "timestamp_or_frame": span.frame_label,
        "template_category": "D",
        "template_id": template_id,
        "response_format": response_format,
        "question_type": question_type,
        "question": question,
        "answer": answer,
        "rationale": rationale,
        "expected_response": build_expected_response(response_format, template_id, meta, span),
        "scoring_rubric": response_scoring_rubric(response_format),
        "visible_evidence": "Pending model or human visual description." if not model_output else "Sampled frames were provided to the VLM.",
        "source_labels_used": source_labels,
        "skill_domain": sorted({LOW_SCORE_FEEDBACK[weakest_subscore(meta)[0]][0], "coaching feedback"}),
        "learner_level": "resident",
        "confidence": "medium",
        "evidence_status": "partially_supported" if template_id != "D4" else "supported_by_labels",
        "requires_expert_review": True,
        "coaching": {
            "feedback_type": feedback_type,
            "feedback_points": [{"priority": priority, "message": answer}],
            "immediate_action": answer if template_id in {"D1", "D3"} else None,
            "practice_drill": drill,
            "tone": "supportive" if template_id in {"D1", "D4", "D5"} else "direct",
        },
        "source_annotation": {
            "dataset_root": str(dataset_root),
            "trial_id": trial_id,
            "capture": capture,
            "gesture_id": span.gesture_id,
            "start_frame": span.start_frame,
            "end_frame": span.end_frame,
            "skill_level": meta.skill_level,
            "grs_total": meta.grs_total,
            "grs_subscores": meta.grs_subscores,
        },
        "model": {
            "model_id": model_id,
            "output": model_output,
            "sampled_frames": sampled_frames or [],
        },
    }


def build_model_prompt(record: dict[str, Any]) -> str:
    annotation = record["source_annotation"]
    expected = record["answer"]
    response_format = record["response_format"]
    if response_format == "taxonomy_structured":
        format_instructions = f"""Return strict JSON with exactly these keys:
- taxonomy_version: "{TAXONOMY_VERSION}"
- taxonomy_labels: object with anatomy, error_type, next_operative_step, complication, coaching_feedback_category
- short_explanation: one sentence
- supporting_evidence_span: object with type, start_frame, end_frame, gesture_id, evidence_source, evidence_status
- uncertainty: one short sentence

Use only this taxonomy for coaching_feedback_category:
tissue_handling, needle_control, economy_of_motion, procedural_flow, overall_technique, final_product_quality, positive_reinforcement_<subscore_name>, insufficient_visual_evidence.

Use only this taxonomy for complication:
none_visible_or_not_annotated, not_assessable_from_clip.
"""
    else:
        format_instructions = """Return strict JSON with exactly these keys:
- best_next_step: one concrete action the trainee should take next
- one_sentence_rationale: exactly one sentence tying the step to the labels and/or visible evidence
- supporting_evidence_span: object with type, start_frame, end_frame, gesture_id, evidence_source, evidence_status
- uncertainty: one short sentence
"""

    return f"""You are reviewing sampled frames from a JIGSAWS Suturing simulation clip.

Task: produce Template D coaching feedback.

Question template source:
Prompts_And_Pipeline/template-d-question-templates.md

Response format:
{response_format}

Question:
{record["question"]}

Available labels:
- trial_id: {annotation["trial_id"]}
- frame_range: {record["timestamp_or_frame"]}
- gesture_id: {annotation["gesture_id"]}
- skill_level: {annotation["skill_level"]}
- grs_total: {annotation["grs_total"]}
- grs_subscores: {json.dumps(annotation["grs_subscores"], sort_keys=True)}

Label-supported expected coaching target:
{expected}

Instructions:
1. Inspect the sampled frames.
2. Do not invent anatomy, bleeding, complications, or patient-specific risk.
3. If the frames do not visibly support the label-supported target, say so.
4. Keep the output concise enough for component-wise scoring.

{format_instructions}
"""


def frame_indices(video_len: int, span: GestureSpan, num_frames: int) -> list[int]:
    start = max(span.start_frame - 1, 0)
    end = min(span.end_frame - 1, video_len - 1)
    if end < start:
        raise ValueError(f"Invalid frame span {span.frame_label} for video with {video_len} frames")

    count = max(1, min(num_frames, end - start + 1))
    if count == 1:
        return [start]
    step = (end - start) / (count - 1)
    return [int(round(start + step * i)) for i in range(count)]


def video_frame_count(video_path: Path) -> int:
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe is required for ffmpeg frame sampling but was not found on PATH.")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip().splitlines()[-1]
    return int(value)


def sample_frames_ffmpeg(video_path: Path, span: GestureSpan, output_dir: Path, num_frames: int) -> list[Path]:
    if span.start_frame is None or span.end_frame is None:
        raise ValueError("Frame sampling requires a concrete frame span.")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for ffmpeg frame sampling but was not found on PATH.")

    output_dir.mkdir(parents=True, exist_ok=True)
    indices = frame_indices(video_frame_count(video_path), span, num_frames)
    frame_paths: list[Path] = []
    for source_idx in indices:
        frame_path = output_dir / f"{video_path.stem}_frame_{source_idx + 1:06d}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-vf",
                f"select=eq(n\\,{source_idx})",
                "-frames:v",
                "1",
                str(frame_path),
            ],
            check=True,
        )
        frame_paths.append(frame_path)
    return frame_paths


def sample_frames_decord(video_path: Path, span: GestureSpan, output_dir: Path, num_frames: int) -> list[Path]:
    if span.start_frame is None or span.end_frame is None:
        raise ValueError("Frame sampling requires a concrete frame span.")
    import decord  # type: ignore
    from PIL import Image

    output_dir.mkdir(parents=True, exist_ok=True)
    reader = decord.VideoReader(str(video_path))
    indices = frame_indices(len(reader), span, num_frames)

    batch = reader.get_batch(indices).asnumpy()
    frame_paths: list[Path] = []
    for source_idx, arr in zip(indices, batch):
        frame_path = output_dir / f"{video_path.stem}_frame_{source_idx + 1:06d}.jpg"
        Image.fromarray(arr).save(frame_path, quality=92)
        frame_paths.append(frame_path)
    return frame_paths


def sample_frames(
    video_path: Path,
    span: GestureSpan,
    output_dir: Path,
    num_frames: int,
    frame_sampler: str,
) -> list[Path]:
    if frame_sampler == "decord":
        return sample_frames_decord(video_path, span, output_dir, num_frames)
    return sample_frames_ffmpeg(video_path, span, output_dir, num_frames)


def run_qwen(
    model_id: str,
    prompt: str,
    frame_paths: list[Path],
    max_new_tokens: int,
    device_map: str,
) -> str:
    import torch
    from qwen_vl_utils import process_vision_info
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    model_kwargs: dict[str, Any] = {"device_map": device_map, "torch_dtype": "auto"}
    if torch.cuda.is_available():
        model_kwargs["torch_dtype"] = torch.bfloat16

    processor = AutoProcessor.from_pretrained(model_id, min_pixels=256 * 28 * 28, max_pixels=1280 * 28 * 28)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_id, **model_kwargs)

    content: list[dict[str, Any]] = [{"type": "image", "image": path.resolve().as_uri()} for path in frame_paths]
    content.append({"type": "text", "text": prompt})
    messages = [{"role": "user", "content": content}]

    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
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

    generated_ids = model.generate(**inputs, max_new_tokens=max_new_tokens)
    generated_ids_trimmed = [
        out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
    ]
    output_text = processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )
    return output_text[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default="/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing")
    parser.add_argument("--trial-id", default="Suturing_B001")
    parser.add_argument("--capture", default="capture1", choices=["capture1", "capture2"])
    parser.add_argument("--template-id", default="D1", choices=sorted(QUESTION_TYPES))
    parser.add_argument(
        "--response-format",
        default="component_structured",
        choices=sorted(RESPONSE_FORMATS),
        help="Structured free-response format for the expected answer and model prompt.",
    )
    parser.add_argument("--frame-range", default=None, help="Optional start-end frame span, e.g. 371-590.")
    parser.add_argument("--gesture-id", default=None, help="Optional gesture id to choose when frame span is omitted.")
    parser.add_argument("--model-id", default="Qwen/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--output-jsonl", default="outputs/jigsaws-suturing-template-d-first-run.jsonl")
    parser.add_argument("--frame-output-dir", default="outputs/jigsaws-suturing-template-d-frames")
    parser.add_argument("--frame-sampler", default="ffmpeg", choices=["ffmpeg", "decord"])
    parser.add_argument("--num-frames", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument(
        "--device-map",
        default="cuda:0",
        help="Model placement for Transformers. Use cuda:0 by default on shared OPrime GPUs.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build prompt/record without loading the model.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_root = Path(args.dataset_root)
    trials = read_meta(dataset_root)
    if args.trial_id not in trials:
        raise KeyError(f"Unknown trial_id {args.trial_id}. Found {len(trials)} trials.")
    meta = trials[args.trial_id]

    if args.template_id == "D4" and not args.frame_range and not args.gesture_id:
        span = GestureSpan(None, None, None)
    else:
        span = choose_span(read_gestures(dataset_root, args.trial_id), args.frame_range, args.gesture_id)

    record = build_record(
        dataset_root=dataset_root,
        trial_id=args.trial_id,
        capture=args.capture,
        template_id=args.template_id,
        response_format=args.response_format,
        span=span,
        meta=meta,
        model_id=args.model_id,
    )
    prompt = build_model_prompt(record)
    record["prompt"] = prompt

    sampled_frame_paths: list[Path] = []
    model_output: str | None = None
    if not args.dry_run:
        video_path = dataset_root / "video" / f"{args.trial_id}_{args.capture}.avi"
        if not video_path.exists():
            raise FileNotFoundError(video_path)
        frame_dir = Path(args.frame_output_dir) / record["qa_id"]
        sampled_frame_paths = sample_frames(video_path, span, frame_dir, args.num_frames, args.frame_sampler)
        model_output = run_qwen(
            args.model_id,
            prompt,
            sampled_frame_paths,
            args.max_new_tokens,
            args.device_map,
        )
        record = build_record(
            dataset_root=dataset_root,
            trial_id=args.trial_id,
            capture=args.capture,
            template_id=args.template_id,
            response_format=args.response_format,
            span=span,
            meta=meta,
            model_id=args.model_id,
            model_output=model_output,
            sampled_frames=[str(path) for path in sampled_frame_paths],
        )
        record["prompt"] = prompt

    output_jsonl = Path(args.output_jsonl)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(json.dumps({
        "wrote": str(output_jsonl),
        "qa_id": record["qa_id"],
        "response_format": record["response_format"],
        "dry_run": args.dry_run,
        "sampled_frames": [str(path) for path in sampled_frame_paths],
        "model_output_preview": model_output[:500] if model_output else None,
    }, indent=2))


if __name__ == "__main__":
    main()
