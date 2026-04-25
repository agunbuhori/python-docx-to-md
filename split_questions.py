#!/usr/bin/env python3
"""Split converted Markdown files into one file per question.

The splitter is intentionally conservative:
- question boundaries are detected from lines beginning with "Pertanyaan..."
  or Markdown headings that contain "Pertanyaan..."
- section headings immediately above a question are kept with that question
- body text is never summarized or shortened
- only footnotes referenced by that question are appended after "---"
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


QUESTION_RE = re.compile(r"^(?:#{1,6}\s+.*?)?Pertanyaan(?:\s+\d+)?\s*:", re.IGNORECASE)
QUESTION_NUMBER_RE = re.compile(r"^(?:#{1,6}\s+.*?)?Pertanyaan(?:\s+(\d+))?\s*:", re.IGNORECASE)
NOTE_DEF_RE = re.compile(r"(?ms)^\[\^(\d+)\]:\s*(.*?)(?=^\[\^\d+\]:|\Z)")
NOTE_REF_RE = re.compile(r"\[\^(\d+)\]")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
IMAGE_FULL_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def split_notes(markdown: str) -> tuple[str, dict[str, str]]:
    separator = re.search(r"(?m)^---\s*$\n\n(?=\[\^\d+\]:)", markdown)
    if not separator:
        return markdown.strip(), {}

    body = markdown[: separator.start()].strip()
    notes_text = markdown[separator.end() :].strip()
    notes = {match.group(1): match.group(2).strip() for match in NOTE_DEF_RE.finditer(notes_text)}
    return body, notes


def question_line_indices(lines: list[str]) -> list[int]:
    return [index for index, line in enumerate(lines) if QUESTION_RE.match(line.strip())]


def context_start(lines: list[str], question_index: int) -> int:
    start = question_index
    index = question_index - 1
    while index >= 0:
        line = lines[index].strip()
        if not line:
            index -= 1
            continue
        if line.startswith("# "):
            start = index
            index -= 1
            continue
        break
    return start


def first_question_title(chunk: str) -> str:
    for line in chunk.splitlines():
        text = line.strip()
        if QUESTION_RE.match(text):
            return clean_heading(text)
    return "pertanyaan"


def question_number(title: str) -> int | None:
    match = QUESTION_NUMBER_RE.match(title.strip())
    if not match or not match.group(1):
        return None
    return int(match.group(1))


def leading_context(chunk: str) -> str:
    lines = chunk.splitlines()
    context: list[str] = []
    for line in lines:
        text = line.strip()
        if QUESTION_RE.match(text):
            break
        context.append(line)
    context_text = "\n".join(context).strip()
    return context_text if context_text.startswith("# ") else ""


def clean_heading(line: str) -> str:
    line = re.sub(r"^#{1,6}\s+", "", line.strip())
    line = re.sub(r"^[0-9]+(?:\.[0-9]+)*\s+—\s+", "", line)
    return line.strip()


def first_section_code(chunk: str) -> str | None:
    for line in chunk.splitlines():
        match = re.match(r"^#{1,6}\s+([0-9]+(?:\.[0-9]+)*)\b", line.strip())
        if match:
            return match.group(1)
    return None


def section_title(chunk: str) -> str:
    for line in chunk.splitlines():
        text = line.strip()
        if text.startswith("# "):
            return clean_heading(text)
    return ""


def renumber_heading(chunk: str, question_code: str, title: str) -> str:
    lines = chunk.splitlines()
    leading_section_indexes: list[int] = []
    for index, line in enumerate(lines):
        text = line.strip()
        if QUESTION_RE.match(text):
            replacement = f"# {question_code} — {title}"
            lines[index] = replacement
            for heading_index in reversed(leading_section_indexes):
                del lines[heading_index]
            break
        if text.startswith("# "):
            leading_section_indexes.append(index)
            continue
    return "\n".join(lines).strip()


def slugify(value: str, max_length: int = 80) -> str:
    value = value.lower()
    value = re.sub(r"\[\^\d+\]", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return (value[:max_length].strip("-") or "pertanyaan")


def referenced_notes(chunk: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for note_id in NOTE_REF_RE.findall(chunk):
        if note_id not in seen:
            seen.add(note_id)
            ordered.append(note_id)
    return ordered


def rewrite_image_links(chunk: str, output_dir: Path, source_dir: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        alt = clean_image_alt(match.group(1))
        target = match.group(2)
        if re.match(r"^[a-z]+://", target) or target.startswith(("/", "#")):
            return f"![{alt}]({target})"

        absolute_target = (source_dir / target).resolve()
        try:
            relative_target = absolute_target.relative_to(output_dir.resolve())
        except ValueError:
            relative_target = Path("..") / absolute_target.relative_to(source_dir.resolve())
        return f"![{alt}]({relative_target.as_posix()})"

    chunk = IMAGE_FULL_RE.sub(replace, chunk)
    chunk = re.sub(r"([^\n])(!\[[^\]]*\]\()", r"\1\n\n\2", chunk)
    return chunk


def clean_image_alt(alt: str) -> str:
    alt = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", alt).strip()
    mojibake_markers = ("â", "", "Ù", "Ø", "", "")
    if any(marker in alt for marker in mojibake_markers):
        return ""
    if alt.lower().startswith("hasil gambar untuk"):
        return ""
    return alt


def build_question_files(source: Path, output_dir: Path) -> list[Path]:
    markdown = source.read_text(encoding="utf-8")
    body, notes = split_notes(markdown)
    lines = body.splitlines()
    question_indices = question_line_indices(lines)
    if not question_indices:
        return []

    starts = [context_start(lines, index) for index in question_indices]
    files: list[Path] = []
    doc_number = source.name.split(".", 1)[0].strip()

    active_context = ""
    section_counts: dict[str, int] = {}
    for sequence, start in enumerate(starts, start=1):
        end = starts[sequence] if sequence < len(starts) else len(lines)
        chunk = "\n".join(lines[start:end]).strip()
        if not chunk:
            continue

        current_context = leading_context(chunk)
        if current_context:
            active_context = current_context
        elif active_context:
            chunk = f"{active_context}\n\n{chunk}"

        chunk = rewrite_image_links(chunk, output_dir, source.parent)
        title = first_question_title(chunk)
        section_code = first_section_code(chunk)
        if not section_code:
            section_code = doc_number

        number = question_number(title)
        if number is None:
            number = section_counts.get(section_code, 0) + 1
        section_counts[section_code] = max(section_counts.get(section_code, 0), number)

        question_code = f"{section_code}.{number}"
        display_title = title
        if section_title(chunk):
            display_title = re.sub(r"^Pertanyaan(?:\s+\d+)?\s*:\s*", "", title, flags=re.IGNORECASE).strip()
        chunk = renumber_heading(chunk, question_code, display_title)
        filename = f"{question_code.replace('.', '_')}.md"
        output_path = output_dir / filename

        note_lines = []
        for note_id in referenced_notes(chunk):
            if note_id in notes:
                note_lines.append(f"[^{note_id}]: {notes[note_id]}")

        content = chunk
        if note_lines:
            content = f"{content}\n\n---\n\n" + "\n".join(note_lines)
        output_path.write_text(content.rstrip() + "\n", encoding="utf-8")
        files.append(output_path)

    return files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split Markdown files into one file per question.")
    parser.add_argument(
        "sources",
        nargs="*",
        type=Path,
        default=[
            Path("outputs/3. bekal haji edited.md"),
            Path("outputs/4. bekal haji edited.md"),
            Path("outputs/5. bekal haji edited.md"),
        ],
        help="Markdown files to split",
    )
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("outputs/questions"))
    parser.add_argument("--clean", action="store_true", help="Remove the output directory before writing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for source in args.sources:
        files = build_question_files(source, output_dir)
        print(f"{source}: {len(files)} files")
        total += len(files)
    print(f"Total: {total} files -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
