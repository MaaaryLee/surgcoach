#!/usr/bin/env python3
"""Generate thinking-enabled B1/B3/B4/B5 QA from selected safety annotations.

The manifest uses three independently collected sources where their labels are
actually suitable:

* BernBypass70 and StrasBypass70: discrete intraoperative adverse events,
  severity, timing, and phase/step context from MultiBypass140.
* Endoscapes2023: per-frame Critical View of Safety criteria and optional
  anatomy/tool class presence.

No image or video is sent to the language model. B4 is therefore limited to
negative-control adverse-event examples (an actual documented event is not a
near miss), and B4/B5 rationales are marked for later visual verification.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_annotation_qa_jigsaws import (  # noqa: E402
    MOCK_WATERMARK,
    extract_system_prompt,
    first_sentence,
    load_text_model,
    log,
    normalize_qa_objects,
    parse_json_payload,
    run_generation,
    run_generation_llama,
    run_generation_ollama,
    thinking_trace_returned,
    verify_llama_server,
    verify_ollama_model,
)
from run_annotation_qa_multibypass140 import (  # noqa: E402
    extract_iae_events,
    find_video_record,
)


SUPPORTED_TEMPLATES = {"B1", "B3", "B4", "B5"}
ENDOSCAPES_FPS = 25
VISUAL_MARKER = "[VISUAL EVIDENCE NEEDED]"
MULTIBYPASS_DATASETS = {
    "BernBypass70": "bern",
    "StrasBypass70": "strasbourg",
}


def timestamp(seconds: int) -> str:
    minutes, remaining = divmod(int(seconds), 60)
    return f"{minutes}:{remaining:02d}"


def load_template_questions(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    questions: dict[str, str] = {}
    for template_id in sorted(SUPPORTED_TEMPLATES):
        match = re.search(
            rf"^## Template {template_id}:[^\n]*\n\n([^\n]+)",
            text,
            flags=re.MULTILINE,
        )
        if not match:
            raise ValueError(f"Could not find canonical {template_id} question in {path}")
        questions[template_id] = match.group(1).strip()
    return questions


def load_id_names(path: Path) -> dict[int, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.reader(handle)
        next(rows)
        return {int(row[0].strip()): row[1].strip() for row in rows}


def event_timestamp(event: dict[str, Any]) -> str:
    start = timestamp(int(event["start_frame"]))
    end = timestamp(int(event["end_frame"]))
    return start if start == end else f"{start}-{end}"


def event_details(
    event: dict[str, Any],
    phase_names: dict[int, str],
    step_names: dict[int, str],
) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "category": event["category"],
        "severity": event["severity"],
        "rectified": event["rectified"],
        "phase_id": event["phase_id"],
        "phase_name": phase_names.get(event["phase_id"]),
        "step_id": event["step_id"],
        "step_name": step_names.get(event["step_id"]),
        "event_second_range": (
            f"{event['start_frame']}-{event['end_frame']}"
        ),
        "event_timestamp": event_timestamp(event),
    }


def load_multibypass_selection(
    item: dict[str, Any], root: Path
) -> tuple[dict[str, Any], str, str]:
    center = item.get("center") or MULTIBYPASS_DATASETS[item["dataset"]]
    pickle_path, frames = find_video_record(
        root / "IAE",
        center,
        item.get("split"),
        item["video_id"],
    )
    events = extract_iae_events(frames)
    phase_names = load_id_names(root / "tables" / "phase.csv")
    step_names = load_id_names(root / "tables" / "step.csv")

    if item["template_id"] == "B3":
        if item.get("scope") != "video":
            raise ValueError(f"B3 MultiBypass item must use video scope: {item}")
        if len({event["category"] for event in events}) < 2:
            raise ValueError(
                f"B3 video {item['video_id']} needs at least two event categories"
            )
        details = [
            event_details(event, phase_names, step_names)
            for event in events
        ]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for detail in details:
            grouped[detail["category"]].append(detail)
        category_summary = []
        for category, category_events in grouped.items():
            category_summary.append(
                {
                    "category": category,
                    "maximum_severity": max(
                        int(event["severity"] or 0)
                        for event in category_events
                    ),
                    "event_count": len(category_events),
                    "all_rectified": all(
                        bool(event["rectified"]) for event in category_events
                    ),
                    "timestamps": [
                        event["event_timestamp"] for event in category_events
                    ],
                    "phase_step_contexts": sorted(
                        {
                            (
                                event["phase_name"],
                                event["step_name"],
                            )
                            for event in category_events
                        }
                    ),
                }
            )
        category_summary.sort(
            key=lambda row: (
                -int(row["maximum_severity"]),
                bool(row["all_rectified"]),
                row["category"],
            )
        )
        time_label = (
            f"{details[0]['event_timestamp']} to "
            f"{details[-1]['event_timestamp']}"
        )
        source = {
            "iae_pickle_file": str(pickle_path),
            "center": center,
            "scope": "whole annotated video",
            "event_count": len(details),
            "distinct_event_categories": sorted(grouped),
            "category_summary": category_summary,
            "events": details,
            "annotation_frequency_fps": 1,
        }
        lines = [
            f"  - video_event_count: {len(details)}",
            (
                "  - distinct_recorded_event_categories: "
                f"{json.dumps(sorted(grouped))}"
            ),
            (
                "  - category_ranking_summary: "
                f"{json.dumps(category_summary, ensure_ascii=False)}"
            ),
            (
                "  - event_records_with_timestamps: "
                f"{json.dumps(details, ensure_ascii=False)}"
            ),
        ]
        return source, time_label, "\n".join(lines)

    event = next(
        (
            candidate
            for candidate in events
            if str(candidate["event_id"]) == str(item["event_id"])
        ),
        None,
    )
    if event is None:
        raise ValueError(
            f"Event {item['event_id']} not found for {item['video_id']} "
            f"in {pickle_path}"
        )
    detail = event_details(event, phase_names, step_names)
    time_label = detail["event_timestamp"]
    source = {
        "iae_pickle_file": str(pickle_path),
        "center": center,
        **detail,
        "annotation_frequency_fps": 1,
    }
    lines = [
        f"  - recorded_event_category: {detail['category']}",
        f"  - recorded_event_severity: {detail['severity']}",
        f"  - recorded_event_rectified: {detail['rectified']}",
        f"  - operative_phase: {detail['phase_name']}",
        f"  - operative_step: {detail['step_name']}",
        f"  - event_timestamp: {time_label}",
        (
            "  - event_scope_limit: the labels establish that this adverse "
            "event occurred, but do not describe its visual appearance or "
            "physical cause"
        ),
    ]
    return source, time_label, "\n".join(lines)


def load_endoscapes_metadata(
    metadata_csv: Path, video_id: str, frame: int
) -> dict[str, Any]:
    with metadata_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["vid"] == video_id and int(row["frame"]) == frame:
                if row.get("is_ds_keyframe") != "True":
                    raise ValueError(
                        f"Endoscapes selection {video_id}_{frame} is not a CVS keyframe"
                    )
                return {
                    "C1": float(row["C1"]),
                    "C2": float(row["C2"]),
                    "C3": float(row["C3"]),
                    "annotator_1": row.get("cvs_annotator_1"),
                    "annotator_2": row.get("cvs_annotator_2"),
                    "annotator_3": row.get("cvs_annotator_3"),
                }
    raise ValueError(f"Endoscapes keyframe {video_id}_{frame} not found")


def load_endoscapes_classes(
    root: Path, split: str, video_id: str, frame: int
) -> list[str]:
    coco_path = root / split / "annotation_coco.json"
    payload = json.loads(coco_path.read_text(encoding="utf-8"))
    filename = f"{video_id}_{frame}.jpg"
    image = next(
        (candidate for candidate in payload["images"] if candidate["file_name"] == filename),
        None,
    )
    if image is None:
        raise ValueError(f"{filename} not found in {coco_path}")
    category_names = {
        int(category["id"]): category["name"]
        for category in payload["categories"]
    }
    return sorted(
        {
            category_names[int(annotation["category_id"])]
            for annotation in payload["annotations"]
            if annotation["image_id"] == image["id"]
        }
    )


def criterion_text(value: float) -> str:
    if value >= 0.99:
        return f"{value:.2f} (achieved unanimously)"
    if value >= 0.5:
        return f"{value:.2f} (achieved by a majority, not unanimously)"
    if value <= 0.01:
        return f"{value:.2f} (not achieved unanimously)"
    return f"{value:.2f} (not achieved by a majority)"


def load_endoscapes_selection(
    item: dict[str, Any], root: Path
) -> tuple[dict[str, Any], str, str]:
    frame = int(item["frame"])
    split = item["coco_split"]
    values = load_endoscapes_metadata(
        root / "all_metadata.csv",
        str(item["video_id"]),
        frame,
    )
    classes = load_endoscapes_classes(
        root,
        split,
        str(item["video_id"]),
        frame,
    )
    time_label = timestamp(round(frame / ENDOSCAPES_FPS))
    source = {
        "metadata_csv": str(root / "all_metadata.csv"),
        "image_file": str(
            root / split / f"{item['video_id']}_{frame}.jpg"
        ),
        "frame": frame,
        "fps_used_for_timestamp_conversion": ENDOSCAPES_FPS,
        "timestamp": time_label,
        **values,
        "labeled_structure_classes": classes,
        "annotation_scope": (
            "CVS achievement and class presence only; the labels do not "
            "describe appearance, condition, motion, or spatial relationships."
        ),
    }
    lines = [
        f"  - cvs_structures_identified: {criterion_text(values['C1'])}",
        (
            "  - cvs_hepatocystic_triangle_cleared: "
            f"{criterion_text(values['C2'])}"
        ),
        f"  - cvs_gallbladder_separated: {criterion_text(values['C3'])}",
        (
            "  - labeled_structure_or_tool_classes_present: "
            f"{json.dumps(classes)}"
        ),
        (
            "  - class_presence_limit: names only; no appearance, condition, "
            "motion, or position may be inferred"
        ),
    ]
    return source, time_label, "\n".join(lines)


def load_selection(
    item: dict[str, Any], args: argparse.Namespace
) -> tuple[dict[str, Any], str, str]:
    if item["dataset"] in MULTIBYPASS_DATASETS:
        return load_multibypass_selection(
            item,
            Path(args.multibypass_root),
        )
    if item["dataset"] == "Endoscapes2023":
        if item["template_id"] == "B4":
            raise ValueError("Endoscapes has no discrete near-miss annotation")
        return load_endoscapes_selection(
            item,
            Path(args.endoscapes_root),
        )
    raise ValueError(f"Unsupported dataset in manifest: {item['dataset']}")


def expected_question(
    item: dict[str, Any],
    time_label: str,
    questions: dict[str, str],
) -> str:
    question = questions[item["template_id"]].replace("[t]", time_label)
    if "[" in question or "]" in question:
        raise ValueError(f"Unresolved placeholder in question: {question}")
    return question


def recent_openers_block(recent_openers: list[str]) -> list[str]:
    if not recent_openers:
        return []
    return [
        "",
        "Recent answer openings in this template batch; use a genuinely "
        "different natural opening:",
        *[f"- {opener}" for opener in recent_openers[-5:]],
    ]


def build_user_prompt(
    item: dict[str, Any],
    time_label: str,
    annotation_lines: str,
    question: str,
    recent_openers: list[str],
) -> str:
    procedure = (
        "Laparoscopic Roux-en-Y Gastric Bypass"
        if item["dataset"] in MULTIBYPASS_DATASETS
        else "Laparoscopic Cholecystectomy"
    )
    lines = [
        "Dataset-specific input (ANNOTATION-ONLY: no frames, images, or videos are attached):",
        f"- dataset_name: {item['dataset']}",
        f"- procedure_or_task: {procedure}",
        f"- video_id: {item['video_id']}",
        f"- clip_id: {item['selection_id']}",
        f"- timestamp_or_span: {time_label}",
        f"- requested_template_ids: {item['template_id']}",
        "- number_of_questions: 1",
        "- available_annotations:",
        annotation_lines,
        "",
        "Ground the QA pair only in these annotations. No visual content was provided.",
        "Do not invent a visible event, anatomy, instrument position, tissue appearance,",
        "physical cause, or complication not present in the supplied evidence.",
        *recent_openers_block(recent_openers),
        "",
        "Use this exact question text:",
        question,
        "",
        (
            "ANSWER RULE: do not mention a score, subscore, rating, rubric, "
            "assessment, performance metric, severity value or level, CVS "
            "value, annotation, label, metadata, record, dataset, or raw field "
            "name in the answer. Translate all evidence into direct clinical "
            "language. Numeric values and source-field names belong only in "
            "the rationale."
        ),
    ]
    template_id = item["template_id"]
    if template_id == "B1":
        if item["dataset"] in MULTIBYPASS_DATASETS:
            lines += [
                "",
                (
                    "Identify the recorded adverse-event category as the most "
                    "urgent concern. Use phase/step wording to name an anatomical "
                    "site only when it is explicit. Translate the supplied "
                    "severity into urgency without mentioning a severity value, "
                    "level, score, rating, annotation, or record."
                ),
            ]
        else:
            lines += [
                "",
                (
                    "Identify the most urgent unmet CVS prerequisite. Do not "
                    "claim a visible injury or event; the input supports a "
                    "safety-readiness concern only."
                ),
            ]
    elif template_id == "B3":
        if item["dataset"] in MULTIBYPASS_DATASETS:
            lines += [
                "",
                (
                    "Rank only the distinct recorded event categories. Use "
                    "maximum supplied severity, unresolved status, and operative "
                    "context to set priority. Do not list the same category more "
                    "than once and do not add an unrecorded generic risk."
                ),
            ]
        else:
            lines += [
                "",
                (
                    "Rank only the unmet or partially met CVS prerequisites. "
                    "Lower achievement has higher priority. For a tie, use this "
                    "conservative prerequisite order: structure identification, "
                    "hepatocystic-triangle clearance, gallbladder separation. "
                    "Do not add an injury that is not recorded."
                ),
            ]
    elif template_id == "B4":
        lines += [
            "",
            (
                "This selection contains a documented adverse event, not a "
                "near-miss label. Begin the answer with \"No.\" Distinguish an "
                "actual event from a narrowly avoided injury. Do not reinterpret "
                "a mild or brief event as a near miss."
            ),
            (
                f"Put {VISUAL_MARKER} exactly once at the very end of the "
                "rationale and nowhere in the answer, because the question asks "
                "what the clip shows but no clip was sent to the model."
            ),
        ]
    elif template_id == "B5":
        lines += [
            "",
            f"Name the stop interval or timestamp {time_label} in the answer.",
            (
                "Use the adverse-event onset or incomplete CVS state as the "
                "stop trigger. Give only structure/site and corrective coaching "
                "supported by the event phase/step or CVS/anatomy context."
            ),
            (
                f"Put {VISUAL_MARKER} exactly once at the very end of the "
                "rationale and nowhere in the answer, because the requested "
                "visible trigger still requires later VLM verification."
            ),
        ]
    lines += [
        "",
        "Return valid JSON only, following the system prompt schema.",
    ]
    return "\n".join(lines)


ANSWER_META_RE = re.compile(
    r"\b(score|scores|subscore|subscores|rating|ratings|rubric|rubrics|"
    r"annotation|annotations|assessment|assessments|performance metric|"
    r"performance metrics|severity|criterion value|criterion values|label|"
    r"labels|metadata|dataset|record|records|iae|cvs_c[123])\b",
    re.IGNORECASE,
)
RAW_DECIMAL_RE = re.compile(r"(?<![\d:])(?:0|1)\.\d+\b")


def validate_output(
    item: dict[str, Any],
    source: dict[str, Any],
    time_label: str,
    expected: str,
    raw_output: str,
    qa_objects: list[dict[str, str]] | None,
    require_thinking: bool,
) -> str | None:
    if qa_objects is None or len(qa_objects) != 1:
        return "schema_mismatch: expected exactly one question/answer/rationale object"
    qa = qa_objects[0]
    if qa["question"] != expected:
        return "question_mismatch: canonical template wording must match exactly"
    if require_thinking and not thinking_trace_returned(raw_output):
        return "missing_thinking_trace: backend returned no reasoning content"
    answer = qa["answer"]
    rationale = qa["rationale"]
    match = ANSWER_META_RE.search(answer)
    if match:
        return f"answer_meta_leak: {match.group(0)!r}"
    if RAW_DECIMAL_RE.search(answer):
        return f"answer_numeric_value_leak: {RAW_DECIMAL_RE.search(answer).group(0)!r}"
    answer_lower = answer.lower()
    is_mock = MOCK_WATERMARK in answer
    event_terms = {
        "Bleeding": ("bleed", "hemorrhag"),
        "Mechanical injury": (
            "mechanical injury",
            "tissue injury",
            "mechanical trauma",
            "direct tissue trauma",
            "tissue trauma",
        ),
        "Thermal injury": ("thermal",),
        "Ischemic injury": ("ischemi",),
        "Insufficient closure of anastomosis": (
            "insufficient closure",
            "incomplete closure",
            "anastomotic closure",
            "anastomotic integrity",
            "inadequate closure",
            "inadequate sealing",
            "leak",
        ),
    }
    if not is_mock and item["dataset"] in MULTIBYPASS_DATASETS:
        if item["template_id"] == "B3":
            categories = source["distinct_event_categories"]
        else:
            categories = [source["category"]]
        for category in categories:
            if not any(term in answer_lower for term in event_terms[category]):
                return f"event_category_missing_from_answer: {category}"
    elif not is_mock:
        criteria = [
            ("C1", ("structure", "anatom")),
            ("C2", ("triangle", "hepatocystic")),
            ("C3", ("gallbladder", "separation")),
        ]
        values = {name: float(source[name]) for name, _ in criteria}
        if item["template_id"] == "B3":
            required = [
                (name, terms)
                for name, terms in criteria
                if values[name] < 0.99
            ]
        else:
            minimum = min(values.values())
            required = [
                (name, terms)
                for name, terms in criteria
                if abs(values[name] - minimum) < 0.01
            ]
        for name, terms in required:
            if not any(term in answer_lower for term in terms):
                return f"cvs_deficiency_missing_from_answer: {name}"
    if item["template_id"] == "B4":
        if not re.match(r"^\s*No\.", answer):
            return "b4_negative_control_error: answer must begin 'No.'"
        if not (
            rationale.count(VISUAL_MARKER) == 1
            and rationale.rstrip().endswith(VISUAL_MARKER)
            and VISUAL_MARKER not in answer
        ):
            return "b4_visual_marker_format"
    if item["template_id"] == "B5":
        if time_label not in answer:
            return "b5_timestamp_missing"
        if not (
            rationale.count(VISUAL_MARKER) == 1
            and rationale.rstrip().endswith(VISUAL_MARKER)
            and VISUAL_MARKER not in answer
        ):
            return "b5_visual_marker_format"
    return None


def mock_raw_output(
    question: str,
    template_id: str,
    time_label: str,
) -> str:
    if template_id == "B1":
        answer = f"Immediate corrective action is required. {MOCK_WATERMARK}"
        rationale = MOCK_WATERMARK
    elif template_id == "B3":
        answer = f"Rank the documented clinical risks in priority order. {MOCK_WATERMARK}"
        rationale = MOCK_WATERMARK
    elif template_id == "B4":
        answer = f"No. This was an actual adverse event. {MOCK_WATERMARK}"
        rationale = f"{MOCK_WATERMARK}\n\n{VISUAL_MARKER}"
    else:
        answer = f"Pause at {time_label}. {MOCK_WATERMARK}"
        rationale = f"{MOCK_WATERMARK}\n\n{VISUAL_MARKER}"
    return json.dumps(
        [
            {
                "question": question,
                "answer": answer,
                "rationale": rationale,
            }
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--system-prompt", required=True)
    parser.add_argument("--template-questions", required=True)
    parser.add_argument(
        "--templates",
        default="B1,B3,B4,B5",
        help="Comma-separated manifest template IDs to generate",
    )
    parser.add_argument(
        "--selection-ids",
        default=None,
        help="Optional comma-separated manifest selection IDs to run",
    )
    parser.add_argument(
        "--endoscapes-root",
        default="/mnt/sun/shared/datasets/surgical_skill/Endoscapes2023",
    )
    parser.add_argument(
        "--multibypass-root",
        default="/mnt/sun/shared/datasets/surgical_skill/MultiBypass140",
    )
    parser.add_argument("--model-id", default="Qwen/Qwen3.6-35B-A3B")
    parser.add_argument("--backend", choices=["real", "mock"], default="real")
    parser.add_argument(
        "--inference-engine",
        choices=["transformers", "ollama", "llama"],
        default="transformers",
    )
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--ollama-model", default="qwen3.6-35b-a3b")
    parser.add_argument("--ollama-host", default="http://localhost:11434")
    parser.add_argument("--ollama-num-ctx", type=int, default=16384)
    parser.add_argument("--llama-host", default="http://localhost:8080")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-new-tokens", type=int, default=7000)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    selected_templates = {
        value.strip() for value in args.templates.split(",") if value.strip()
    }
    unknown = selected_templates - SUPPORTED_TEMPLATES
    if unknown:
        parser.error(f"Unsupported template IDs: {sorted(unknown)}")

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = [
        item for item in manifest if item["template_id"] in selected_templates
    ]
    if args.selection_ids:
        selected_ids = {
            value.strip()
            for value in args.selection_ids.split(",")
            if value.strip()
        }
        manifest = [
            item for item in manifest if item["selection_id"] in selected_ids
        ]
    if not manifest:
        parser.error("No manifest selections match --templates")
    if len({item["selection_id"] for item in manifest}) != len(manifest):
        parser.error("Manifest selection_id values must be unique")

    system_prompt_path = Path(args.system_prompt)
    system_prompt = extract_system_prompt(system_prompt_path)
    system_prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
    template_path = Path(args.template_questions)
    questions = load_template_questions(template_path)
    template_hash = hashlib.sha256(template_path.read_bytes()).hexdigest()

    tokenizer = model = None
    if args.backend == "real":
        if args.inference_engine == "llama":
            verify_llama_server(args.llama_host)
        elif args.inference_engine == "ollama":
            verify_ollama_model(args.ollama_host, args.ollama_model)
        else:
            tokenizer, model = load_text_model(args.model_id, args.device_map)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = (
        "qa_records.jsonl"
        if args.backend == "real"
        else "qa_records.mock.jsonl"
    )
    output_path = output_dir / output_name
    valid = rejected = thinking_verified = 0
    recent_openers: dict[str, list[str]] = defaultdict(list)

    with output_path.open("w", encoding="utf-8") as handle:
        for item in manifest:
            source, time_label, annotation_lines = load_selection(item, args)
            question = expected_question(item, time_label, questions)
            user_prompt = build_user_prompt(
                item,
                time_label,
                annotation_lines,
                question,
                recent_openers[item["template_id"]],
            )
            attempts = 0
            earlier_errors: list[str] = []
            qa_objects = None
            raw_output = ""
            error = None
            while True:
                attempts += 1
                if args.backend == "mock":
                    raw_output = mock_raw_output(
                        question,
                        item["template_id"],
                        time_label,
                    )
                elif args.inference_engine == "llama":
                    raw_output = run_generation_llama(
                        args.llama_host,
                        system_prompt,
                        user_prompt,
                        args.max_new_tokens,
                        args.temperature,
                    )
                elif args.inference_engine == "ollama":
                    raw_output = run_generation_ollama(
                        args.ollama_host,
                        args.ollama_model,
                        system_prompt,
                        user_prompt,
                        args.max_new_tokens,
                        args.ollama_num_ctx,
                        args.temperature,
                    )
                else:
                    raw_output = run_generation(
                        tokenizer,
                        model,
                        system_prompt,
                        user_prompt,
                        args.max_new_tokens,
                        args.temperature,
                    )
                qa_objects = None
                error = None
                try:
                    qa_objects = normalize_qa_objects(
                        parse_json_payload(raw_output)
                    )
                except Exception as exc:  # noqa: BLE001
                    error = repr(exc)
                if error is None:
                    error = validate_output(
                        item,
                        source,
                        time_label,
                        question,
                        raw_output,
                        qa_objects,
                        require_thinking=args.backend == "real",
                    )
                retryable = (
                    error is not None
                    and attempts <= args.max_retries
                    and args.backend == "real"
                    and args.temperature > 0
                )
                if not retryable:
                    break
                earlier_errors.append(error)
                log(f"retry {attempts}/{args.max_retries} after: {error}")

            returned_thinking = (
                args.backend == "real"
                and thinking_trace_returned(raw_output)
            )
            if error is None:
                status = "valid" if args.backend == "real" else "valid_mock"
                valid += 1
                if returned_thinking:
                    thinking_verified += 1
                if qa_objects:
                    recent_openers[item["template_id"]].append(
                        first_sentence(qa_objects[0]["answer"])
                    )
            else:
                status = "rejected"
                rejected += 1

            record = {
                "qa_id": (
                    f"{item['dataset'].lower()}_{item['template_id'].lower()}_"
                    f"{item['selection_id']}_anno"
                ),
                "selection_id": item["selection_id"],
                "dataset": item["dataset"],
                "procedure_or_task": (
                    "Laparoscopic Roux-en-Y Gastric Bypass"
                    if item["dataset"] in MULTIBYPASS_DATASETS
                    else "Laparoscopic Cholecystectomy"
                ),
                "generation_mode": "annotation_only",
                "template_id": item["template_id"],
                "video_id": item["video_id"],
                "timestamp_or_frame": time_label,
                "selection_basis": item.get("selection_basis"),
                "source_annotation": source,
                "model": {
                    "backend": args.backend,
                    "real_model_inference": args.backend == "real",
                    "inference_engine": (
                        args.inference_engine
                        if args.backend == "real"
                        else None
                    ),
                    "model_id": (
                        (
                            "ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
                            if args.inference_engine == "llama"
                            else (
                                args.ollama_model
                                if args.inference_engine == "ollama"
                                else args.model_id
                            )
                        )
                        if args.backend == "real"
                        else None
                    ),
                    "system_prompt_path": str(system_prompt_path),
                    "system_prompt_sha256": system_prompt_hash,
                    "template_questions_path": str(template_path),
                    "template_questions_sha256": template_hash,
                    "thinking": {
                        "enabled": args.backend == "real",
                        "required": args.backend == "real",
                        "request_parameter": (
                            {"chat_template_kwargs": {"enable_thinking": True}}
                            if args.backend == "real"
                            and args.inference_engine == "llama"
                            else (
                                {"think": True}
                                if args.backend == "real"
                                and args.inference_engine == "ollama"
                                else (
                                    {"enable_thinking": True}
                                    if args.backend == "real"
                                    else None
                                )
                            )
                        ),
                        "trace_returned": returned_thinking,
                    },
                    "attempts": attempts,
                    "retried_after_errors": earlier_errors or None,
                    "prompt": user_prompt,
                    "raw_output": raw_output,
                },
                "qa": qa_objects,
                "validation_status": status,
                "validation_error": error,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            log(
                f"{record['qa_id']}: {status}"
                + (f" ({error})" if error else "")
            )

    summary = {
        "backend": args.backend,
        "templates": sorted(selected_templates),
        "selections": len(manifest),
        "valid": valid,
        "rejected": rejected,
        "thinking_required": args.backend == "real",
        "thinking_verified": thinking_verified,
        "system_prompt_path": str(system_prompt_path),
        "system_prompt_sha256": system_prompt_hash,
        "template_questions_path": str(template_path),
        "template_questions_sha256": template_hash,
        "output": str(output_path),
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
