#!/usr/bin/env python3
"""Combine per-source item folders into one folder and one JSON index."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def rewrite_markdown_images(markdown: str, source_dir: Path, output_dir: Path, source_id: str) -> str:
    images_dir = output_dir / "assets" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    def replace(match: re.Match[str]) -> str:
        alt = match.group(1)
        target = match.group(2)
        if re.match(r"^[a-z]+://", target) or target.startswith(("/", "#")):
            return match.group(0)

        source_image = source_dir / target
        if not source_image.exists():
            return match.group(0)

        new_name = f"{source_id}_{source_image.name}"
        output_image = images_dir / new_name
        shutil.copy2(source_image, output_image)
        return f"![{alt}](assets/images/{new_name})"

    return IMAGE_RE.sub(replace, markdown)


def copy_thumbnail(thumbnail: str, source_dir: Path, output_dir: Path, source_id: str) -> str:
    if not thumbnail or re.match(r"^[a-z]+://", thumbnail) or thumbnail.startswith(("/", "#")):
        return thumbnail

    source_image = source_dir / thumbnail
    if not source_image.exists():
        return thumbnail

    images_dir = output_dir / "assets" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    new_name = f"{source_id}_{source_image.name}"
    output_image = images_dir / new_name
    if not output_image.exists():
        shutil.copy2(source_image, output_image)
    return f"assets/images/{new_name}"


def combine(source_dirs: list[Path], output_dir: Path) -> list[dict[str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    combined_index: list[dict[str, str]] = []

    for source_dir in source_dirs:
        index_path = source_dir / "index.json"
        if not index_path.exists():
            continue

        items = json.loads(index_path.read_text(encoding="utf-8"))
        source_id = str(items[0].get("source") if items else source_dir.name)

        for item in items:
            source_file = source_dir / item["file"]
            if not source_file.exists():
                continue

            output_file_name = f"{item['source']}_{Path(item['file']).name}"
            output_file = output_dir / output_file_name
            markdown = source_file.read_text(encoding="utf-8")
            markdown = rewrite_markdown_images(markdown, source_dir, output_dir, source_id)
            output_file.write_text(markdown, encoding="utf-8")

            combined_item = dict(item)
            combined_item["file"] = output_file_name
            combined_item["thumbnail"] = copy_thumbnail(
                item.get("thumbnail", ""),
                source_dir,
                output_dir,
                source_id,
            )
            combined_index.append(combined_item)

    return combined_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Combine item folders into one folder and index.json.")
    parser.add_argument(
        "source_dirs",
        nargs="*",
        type=Path,
        default=[
            Path("outputs/items/1"),
            Path("outputs/items/2"),
            Path("outputs/items/3"),
            Path("outputs/items/4"),
        ],
    )
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("outputs/items/all"))
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.clean and args.output_dir.exists():
        shutil.rmtree(args.output_dir)

    index = combine(args.source_dirs, args.output_dir)
    index_path = args.output_dir / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(index)} item(s) -> {args.output_dir}")
    print(f"Index -> {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
