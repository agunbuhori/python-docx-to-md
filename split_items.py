#!/usr/bin/env python3
"""Split a Markdown document into item files and build a JSON index.

Markdown documents in this project are not fully consistent. The splitter uses
these item boundaries, in order:
- level-1 headings ("# ...")
- level-2 headings ("## ...") when the document does not use level-1 item headings
- plain place-name lines followed by an image or "Lokasi:"
- numbered well names such as "1. Sumur Utsman"

The output files are named 1.md, 2.md, and so on. The first image inside each
item becomes the thumbnail in index.json.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
NOTE_DEF_RE = re.compile(r"(?ms)^\[\^(\d+)\]:\s*(.*?)(?=^\[\^\d+\]:|\Z)")
NOTE_REF_RE = re.compile(r"\[\^(\d+)\]")

NON_ITEM_TITLES = {
    "catatan",
    "gambar",
    "hadis terkait",
    "hadis-hadis terkait",
    "lokasi",
    "makna harrah",
    "pelajaran",
    "pelajaran dan faedah",
    "penjelasan",
}


def split_notes(markdown: str) -> tuple[str, dict[str, str]]:
    separator = re.search(r"(?m)^---\s*$\n\n(?=\[\^\d+\]:)", markdown)
    if not separator:
        return markdown.strip(), {}
    body = markdown[: separator.start()].strip()
    notes_text = markdown[separator.end() :].strip()
    notes = {match.group(1): match.group(2).strip() for match in NOTE_DEF_RE.finditer(notes_text)}
    return body, notes


def clean_name(value: str) -> str:
    value = re.sub(r"\[\^\d+\]", "", value)
    value = re.sub(r"^\d+\.\s+", "", value)
    value = re.sub(r"\s*\(\s*\)\s*$", "", value)
    return value.strip().rstrip(":").strip()


def sanitize_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value or "image"


def clean_image_alt(value: str) -> str:
    value = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", value).strip()
    if value.lower() in {"undefined", "null", "none"}:
        return ""
    if value.lower().startswith("hasil gambar untuk"):
        return ""
    if any(marker in value for marker in ("â", "", "Ù", "Ø", "", "")):
        return ""
    return value


def meaningful_next(lines: list[str], index: int, limit: int = 4) -> list[str]:
    found: list[str] = []
    for line in lines[index + 1 : index + 1 + limit * 2]:
        text = line.strip()
        if text:
            found.append(text)
        if len(found) >= limit:
            break
    return found


def is_numbered_well(text: str) -> bool:
    return bool(re.match(r"^\d+\.\s+Sumur\b", text, re.IGNORECASE))


def is_plain_place_title(lines: list[str], index: int) -> bool:
    text = lines[index].strip()
    if not text or len(text) > 90:
        return False
    if text.startswith(("#", "-", "*", ">", "|", "```", "![")):
        return False
    if text == "()":
        return False
    if not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", text):
        return False
    if text.lower().startswith(("lokasi:", "gambar:", "penjelasan:", "catatan:")):
        return False
    if text.endswith(":"):
        return False
    if re.match(r"^\d+\.\s+", text) and not is_numbered_well(text):
        return False
    if text.lower() in NON_ITEM_TITLES:
        return False

    next_lines = meaningful_next(lines, index)
    if not next_lines:
        return False
    return any(line.startswith("![") or line.lower().startswith("lokasi:") for line in next_lines[:3])


def item_name_for_line(line: str) -> str | None:
    text = line.strip()
    heading = HEADING_RE.match(text)
    if heading:
        return clean_name(heading.group(2))
    if is_numbered_well(text):
        return clean_name(text)
    return clean_name(text) if text else None


def item_start_indices(lines: list[str]) -> list[int]:
    heading_levels = [
        len(match.group(1))
        for line in lines
        if (match := HEADING_RE.match(line.strip()))
    ]
    h1_count = heading_levels.count(1)

    starts: list[int] = []
    for index, line in enumerate(lines):
        text = line.strip()
        heading = HEADING_RE.match(text)
        if heading:
            level = len(heading.group(1))
            if level == 1 or (h1_count == 0 and level == 2):
                starts.append(index)
            continue
        if is_numbered_well(text) or (h1_count == 0 and is_plain_place_title(lines, index)):
            starts.append(index)

    deduped: list[int] = []
    seen: set[int] = set()
    for start in sorted(starts):
        if start not in seen:
            deduped.append(start)
            seen.add(start)
    return deduped


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def rewrite_images(chunk: str, source_dir: Path, images_dir: Path, item_number: int) -> tuple[str, str]:
    first_image = ""

    def replace(match: re.Match[str]) -> str:
        nonlocal first_image
        alt = clean_image_alt(match.group(1))
        target = match.group(2)
        if re.match(r"^[a-z]+://", target) or target.startswith(("/", "#")):
            if not first_image:
                first_image = target
            return match.group(0)

        source_image = (source_dir / target).resolve()
        if not source_image.exists():
            return match.group(0)

        output_name = sanitize_filename(f"{item_number}_{source_image.name}")
        output_image = unique_path(images_dir / output_name)
        output_image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_image, output_image)

        rel_target = f"assets/images/{output_image.name}"
        if not first_image:
            first_image = rel_target
        return f"![{alt}]({rel_target})"

    rewritten = IMAGE_RE.sub(replace, chunk)
    return rewritten, first_image


def referenced_notes(chunk: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for note_id in NOTE_REF_RE.findall(chunk):
        if note_id not in seen:
            seen.add(note_id)
            ordered.append(note_id)
    return ordered


def split_items(source: Path, output_dir: Path, source_id: str) -> list[dict[str, str]]:
    markdown = source.read_text(encoding="utf-8")
    body, notes = split_notes(markdown)
    lines = body.splitlines()
    starts = item_start_indices(lines)
    if not starts:
        return []

    images_dir = output_dir / "assets" / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    index: list[dict[str, str]] = []
    for sequence, start in enumerate(starts, start=1):
        end = starts[sequence] if sequence < len(starts) else len(lines)
        chunk = "\n".join(lines[start:end]).strip()
        name = item_name_for_line(lines[start])
        if not chunk or not name:
            continue

        chunk, thumbnail = rewrite_images(chunk, source.parent, images_dir, sequence)

        note_lines = []
        for note_id in referenced_notes(chunk):
            if note_id in notes:
                note_lines.append(f"[^{note_id}]: {notes[note_id]}")
        if note_lines:
            chunk = f"{chunk.rstrip()}\n\n---\n\n" + "\n".join(note_lines)

        output_file = output_dir / f"{sequence}.md"
        output_file.write_text(chunk.rstrip() + "\n", encoding="utf-8")

        index.append(
            {
                "thumbnail": thumbnail,
                "name": name,
                "source": source_id,
                "file": f"{sequence}.md",
            }
        )

    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split level-1 Markdown sections into item files.")
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("outputs/items"))
    parser.add_argument("--source-id", default="1")
    parser.add_argument("--index-name", default="index.json")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.clean and args.output_dir.exists():
        shutil.rmtree(args.output_dir)

    index = split_items(args.source, args.output_dir, args.source_id)
    index_path = args.output_dir / args.index_name
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(index)} item(s) -> {args.output_dir}")
    print(f"Index -> {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
