#!/usr/bin/env python3
"""Current SurgCoach system prompt and Template D question rendering helpers."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_PROMPT_PATH = REPO_ROOT / "Prompts_And_Pipeline" / "system-prompt-jigsaws-v1.1.md"
TEMPLATE_D_QUESTION_PATH = REPO_ROOT / "Prompts_And_Pipeline" / "template-d-question-templates.md"


def load_system_prompt(path: Path = SYSTEM_PROMPT_PATH) -> str:
    """Load the current system prompt Markdown exactly."""
    return path.read_text(encoding="utf-8")


SYSTEM_PROMPT = load_system_prompt()


def load_template_d_questions(path: Path = TEMPLATE_D_QUESTION_PATH) -> dict[str, str]:
    """Load D1-D5 question wording from the canonical Markdown file."""
    text = path.read_text(encoding="utf-8")
    templates: dict[str, str] = {}
    for match in re.finditer(r"^## Template (D[1-5]): .+\n\n(.+)$", text, re.MULTILINE):
        templates[match.group(1)] = match.group(2).strip()
    missing = sorted(set(f"D{index}" for index in range(1, 6)) - set(templates))
    if missing:
        raise ValueError(f"Missing Template D question wording in {path}: {missing}")
    return templates


def render_template_d_question(template_id: str, video_span: str) -> str:
    """Render one Template D question with the provided span substituted."""
    templates = load_template_d_questions()
    try:
        template = templates[template_id]
    except KeyError as exc:
        raise ValueError(f"Unknown template_id {template_id!r}; expected D1-D5.") from exc
    return template.replace("[a certain video span]", video_span)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-id", choices=[f"D{index}" for index in range(1, 6)])
    parser.add_argument("--video-span", default="[a certain video span]")
    parser.add_argument("--json", action="store_true", help="Print paths, system prompt, and rendered question as JSON.")
    args = parser.parse_args()

    rendered_question = None
    if args.template_id:
        rendered_question = render_template_d_question(args.template_id, args.video_span)

    if args.json:
        print(
            json.dumps(
                {
                    "system_prompt_path": str(SYSTEM_PROMPT_PATH),
                    "template_d_question_path": str(TEMPLATE_D_QUESTION_PATH),
                    "system_prompt": SYSTEM_PROMPT,
                    "template_id": args.template_id,
                    "rendered_question": rendered_question,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    print(SYSTEM_PROMPT)
    if rendered_question:
        print("\nRendered question:")
        print(rendered_question)


if __name__ == "__main__":
    main()
