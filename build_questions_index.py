#!/usr/bin/env python3
"""Build a JSON index for split question Markdown files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


QUESTION_HEADING_RE = re.compile(r"^#\s+([0-9]+(?:\.[0-9]+)*)\s+—\s+(.+?)\s*$")
SECTION_HEADING_RE = re.compile(r"^#\s+([0-9]+(?:\.[0-9]+)*)\s+—\s+(.+?)\s*$")


def clean_title(value: str) -> str:
    value = re.sub(r"\[\^\d+\]", "", value).strip()
    value = re.sub(r"^Pertanyaan(?:\s+\d+)?\s*:\s*", "", value, flags=re.IGNORECASE).strip()
    return value.rstrip(".").strip()


def numeric_key(code: str) -> tuple[int, ...]:
    return tuple(int(part) for part in code.split("."))


def section_code_for_answer(answer_code: str) -> str:
    parts = answer_code.split(".")
    if len(parts) <= 1:
        return answer_code
    return ".".join(parts[:-1])


def read_section_categories(sources: list[Path]) -> dict[str, str]:
    categories: dict[str, str] = {}
    for source in sources:
        if not source.exists():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            match = SECTION_HEADING_RE.match(line.strip())
            if not match:
                continue
            code, title = match.groups()
            title = clean_title(title)
            if "Pertanyaan" in title:
                continue
            # Keep only top-level question categories such as 4.10, not inserted
            # explanatory subheadings such as 4.10.1.1.
            if len(code.split(".")) <= 2:
                categories[code] = title
    return categories


def question_from_file(path: Path) -> tuple[str, str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = QUESTION_HEADING_RE.match(line.strip())
        if match:
            answer_code, title = match.groups()
            return answer_code, clean_title(title)
    raise ValueError(f"No question heading found in {path}")


def build_index(questions_dir: Path, source_files: list[Path]) -> list[dict[str, str]]:
    categories = read_section_categories(source_files)
    items: list[dict[str, str]] = []
    for path in sorted(questions_dir.glob("*.md")):
        answer_code, question = question_from_file(path)
        category = categories.get(section_code_for_answer(answer_code), "Lain-lain")
        items.append(
            {
                "question": question,
                "answer": answer_code,
                "category": category,
            }
        )
    return sorted(items, key=lambda item: numeric_key(item["answer"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build JSON index for question Markdown files.")
    parser.add_argument("--questions-dir", type=Path, default=Path("outputs/questions"))
    parser.add_argument(
        "--sources",
        nargs="*",
        type=Path,
        default=[
            Path("outputs/3. bekal haji edited.md"),
            Path("outputs/4. bekal haji edited.md"),
            Path("outputs/5. bekal haji edited.md"),
        ],
    )
    parser.add_argument("-o", "--output", type=Path, default=Path("outputs/questions_index.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    index = build_index(args.questions_dir, args.sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(index)} questions -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
