#!/usr/bin/env python3
"""Compress JPG/JPEG/PNG images locally, similar to TinyJPG/TinyPNG.

Examples:
    python3 compress_images.py outputs/images -o outputs/images_compressed
    python3 compress_images.py outputs/images -o outputs/images_tiny --tiny
    python3 compress_images.py photo.jpg -o compressed/photo.jpg --quality 78
    python3 compress_images.py outputs/images --in-place

Requires:
    pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ModuleNotFoundError:
    print(
        "Pillow belum terpasang. Jalankan dulu:\n\n"
        "  python3 -m pip install -r requirements.txt\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class CompressResult:
    source: Path
    output: Path
    original_size: int
    compressed_size: int
    changed: bool

    @property
    def saved_bytes(self) -> int:
        return self.original_size - self.compressed_size

    @property
    def saved_percent(self) -> float:
        if self.original_size == 0:
            return 0.0
        return (self.saved_bytes / self.original_size) * 100


def collect_images(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []

    pattern = "**/*" if recursive else "*"
    return sorted(
        file
        for file in path.glob(pattern)
        if file.is_file() and file.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def output_path_for(source: Path, input_root: Path, output_root: Path, in_place: bool) -> Path:
    if in_place:
        return source
    if input_root.is_file():
        return output_root if output_root.suffix else output_root / source.name
    return output_root / source.relative_to(input_root)


def prepare_image(image: Image.Image, extension: str, max_width: int | None = None) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if max_width and image.width > max_width:
        ratio = max_width / image.width
        new_size = (max_width, max(1, round(image.height * ratio)))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    if extension in {".jpg", ".jpeg"}:
        if image.mode in {"RGBA", "LA", "P"}:
            background = Image.new("RGB", image.size, (255, 255, 255))
            if image.mode == "P":
                image = image.convert("RGBA")
            background.paste(image, mask=image.getchannel("A") if "A" in image.getbands() else None)
            return background
        return image.convert("RGB")
    if image.mode == "P":
        return image
    if image.mode not in {"RGB", "RGBA", "LA", "L"}:
        return image.convert("RGBA")
    return image


def quantize_png(image: Image.Image, colors: int) -> Image.Image:
    if image.mode == "P":
        return image
    if "A" in image.getbands():
        return image.convert("RGBA").quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
    return image.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT)


def save_compressed(image: Image.Image, output: Path, quality: int, png_colors: int | None) -> None:
    extension = output.suffix.lower()
    output.parent.mkdir(parents=True, exist_ok=True)

    if extension in {".jpg", ".jpeg"}:
        image.save(
            output,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
        )
    elif extension == ".png":
        if png_colors:
            image = quantize_png(image, png_colors)
        image.save(output, format="PNG", optimize=True, compress_level=9)
    else:
        raise ValueError(f"Unsupported image extension: {extension}")


def compress_image(
    source: Path,
    output: Path,
    quality: int,
    png_colors: int | None,
    max_width: int | None,
    keep_smaller: bool,
) -> CompressResult:
    original_size = source.stat().st_size

    with Image.open(source) as opened:
        image = prepare_image(opened, source.suffix.lower(), max_width=max_width)

        with tempfile.TemporaryDirectory() as temporary_dir:
            temporary_output = Path(temporary_dir) / source.name
            save_compressed(image, temporary_output, quality, png_colors=png_colors)
            compressed_size = temporary_output.stat().st_size

            if keep_smaller and compressed_size >= original_size:
                if source.resolve() != output.resolve():
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, output)
                return CompressResult(source, output, original_size, original_size, False)

            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(temporary_output, output)
            return CompressResult(source, output, original_size, compressed_size, True)


def human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compress JPG/JPEG/PNG images locally.")
    parser.add_argument("input", type=Path, help="Input image file or directory")
    parser.add_argument("-o", "--output", type=Path, default=Path("compressed_images"))
    parser.add_argument("--quality", type=int, default=None, help="JPEG quality, 1-95")
    parser.add_argument(
        "--tiny",
        action="store_true",
        help="Aggressive lossy mode: JPEG quality 55 and PNG quantized to 96 colors",
    )
    parser.add_argument(
        "--png-colors",
        type=int,
        default=None,
        help="Lossy PNG palette size, 2-256. Smaller means stronger compression.",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=None,
        help="Resize images wider than this value before compression.",
    )
    parser.add_argument("--recursive", action="store_true", help="Scan directories recursively")
    parser.add_argument("--in-place", action="store_true", help="Overwrite source files if smaller")
    parser.add_argument(
        "--allow-larger",
        action="store_true",
        help="Keep compressed output even if it is larger than the original",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_root = args.output.expanduser().resolve()

    if not input_path.exists():
        print(f"Input tidak ditemukan: {input_path}", file=sys.stderr)
        return 1
    quality = args.quality if args.quality is not None else (55 if args.tiny else 82)
    png_colors = args.png_colors if args.png_colors is not None else (96 if args.tiny else None)

    if not 1 <= quality <= 95:
        print("--quality harus berada di antara 1 dan 95", file=sys.stderr)
        return 1
    if png_colors is not None and not 2 <= png_colors <= 256:
        print("--png-colors harus berada di antara 2 dan 256", file=sys.stderr)
        return 1
    if args.max_width is not None and args.max_width < 1:
        print("--max-width harus lebih besar dari 0", file=sys.stderr)
        return 1

    images = collect_images(input_path, args.recursive)
    if not images:
        print("Tidak ada file JPG/JPEG/PNG yang ditemukan.")
        return 0

    results: list[CompressResult] = []
    for image in images:
        output = output_path_for(image, input_path, output_root, args.in_place)
        try:
            result = compress_image(
                image,
                output,
                quality=quality,
                png_colors=png_colors,
                max_width=args.max_width,
                keep_smaller=not args.allow_larger,
            )
        except Exception as exc:  # Pillow can raise several image-specific exceptions.
            print(f"SKIP {image}: {exc}", file=sys.stderr)
            continue

        results.append(result)
        status = "OK" if result.changed else "KEPT"
        print(
            f"{status} {image} -> {result.output} "
            f"({human_size(result.original_size)} -> {human_size(result.compressed_size)}, "
            f"{result.saved_percent:.1f}% saved)"
        )

    original_total = sum(result.original_size for result in results)
    compressed_total = sum(result.compressed_size for result in results)
    saved_total = original_total - compressed_total
    saved_percent = (saved_total / original_total * 100) if original_total else 0.0

    print()
    print(f"Processed: {len(results)} image(s)")
    print(f"Original : {human_size(original_total)}")
    print(f"Output   : {human_size(compressed_total)}")
    print(f"Saved    : {human_size(saved_total)} ({saved_percent:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
