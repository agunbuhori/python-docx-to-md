#!/usr/bin/env python3
"""Convert a .docx document to Markdown, including embedded images.

Usage:
    python convert.py input.docx
    python convert.py input.docx -o output.md

Images are extracted next to the Markdown file in an assets directory and
referenced with relative Markdown image links.
"""

from __future__ import annotations

import argparse
import html
import posixpath
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}


def qn(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


@dataclass(frozen=True)
class ImageRef:
    rel_id: str
    alt: str


class DocxToMarkdown:
    def __init__(self, docx_path: Path, output_md: Path, assets_dir: Path) -> None:
        self.docx_path = docx_path
        self.output_md = output_md
        self.assets_dir = assets_dir
        self.assets_link = assets_dir.name
        self.relationships: dict[str, str] = {}
        self.number_formats: dict[tuple[str, str], str] = {}
        self.extracted_images: dict[str, str] = {}
        self.footnotes: dict[str, str] = {}
        self.endnotes: dict[str, str] = {}
        self.note_numbers: dict[tuple[str, str], int] = {}
        self.used_notes: list[tuple[str, str]] = []
        self.used_asset_names: set[str] = set()

    def convert(self) -> str:
        with zipfile.ZipFile(self.docx_path) as docx:
            self.relationships = self._read_relationships(docx)
            self.number_formats = self._read_numbering(docx)
            self.footnotes = self._read_notes(docx, "word/footnotes.xml", "footnote")
            self.endnotes = self._read_notes(docx, "word/endnotes.xml", "endnote")
            document_xml = self._read_required(docx, "word/document.xml")
            root = ET.fromstring(document_xml)
            body = root.find("w:body", NS)
            if body is None:
                raise ValueError("Dokumen tidak memiliki body yang valid.")

            blocks: list[str] = []
            for child in body:
                if child.tag == qn("w", "p"):
                    block = self._paragraph_to_markdown(docx, child)
                elif child.tag == qn("w", "tbl"):
                    block = self._table_to_markdown(docx, child)
                else:
                    block = ""

                if block:
                    blocks.append(block)

        markdown = self._prettify_blocks(blocks)
        notes = self._notes_markdown()
        if notes:
            markdown = f"{markdown.rstrip()}\n\n---\n\n{notes}"
        return markdown.rstrip() + "\n"

    def write(self) -> None:
        markdown = self.convert()
        self.output_md.parent.mkdir(parents=True, exist_ok=True)
        self.output_md.write_text(markdown, encoding="utf-8")

    def _read_required(self, docx: zipfile.ZipFile, name: str) -> bytes:
        try:
            return docx.read(name)
        except KeyError as exc:
            raise ValueError(f"File DOCX tidak valid: {name} tidak ditemukan.") from exc

    def _read_relationships(self, docx: zipfile.ZipFile) -> dict[str, str]:
        try:
            rels_xml = docx.read("word/_rels/document.xml.rels")
        except KeyError:
            return {}

        root = ET.fromstring(rels_xml)
        relationships: dict[str, str] = {}
        for rel in root.findall("rel:Relationship", NS):
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if rel_id and target:
                relationships[rel_id] = posixpath.normpath(posixpath.join("word", target))
        return relationships

    def _read_numbering(self, docx: zipfile.ZipFile) -> dict[tuple[str, str], str]:
        try:
            numbering_xml = docx.read("word/numbering.xml")
        except KeyError:
            return {}

        root = ET.fromstring(numbering_xml)
        abstract_formats: dict[tuple[str, str], str] = {}
        for abstract_num in root.findall("w:abstractNum", NS):
            abstract_id = abstract_num.attrib.get(qn("w", "abstractNumId"))
            if not abstract_id:
                continue
            for level in abstract_num.findall("w:lvl", NS):
                ilvl = level.attrib.get(qn("w", "ilvl"), "0")
                fmt = level.find("w:numFmt", NS)
                abstract_formats[(abstract_id, ilvl)] = (
                    fmt.attrib.get(qn("w", "val"), "bullet") if fmt is not None else "bullet"
                )

        num_to_abstract: dict[str, str] = {}
        for num in root.findall("w:num", NS):
            num_id = num.attrib.get(qn("w", "numId"))
            abstract = num.find("w:abstractNumId", NS)
            if num_id and abstract is not None:
                abstract_id = abstract.attrib.get(qn("w", "val"))
                if abstract_id:
                    num_to_abstract[num_id] = abstract_id

        number_formats: dict[tuple[str, str], str] = {}
        for num_id, abstract_id in num_to_abstract.items():
            for (candidate_id, ilvl), fmt in abstract_formats.items():
                if candidate_id == abstract_id:
                    number_formats[(num_id, ilvl)] = fmt
        return number_formats

    def _read_notes(self, docx: zipfile.ZipFile, name: str, tag_name: str) -> dict[str, str]:
        try:
            notes_xml = docx.read(name)
        except KeyError:
            return {}

        root = ET.fromstring(notes_xml)
        notes: dict[str, str] = {}
        for note in root.findall(f"w:{tag_name}", NS):
            note_id = note.attrib.get(qn("w", "id"))
            note_type = note.attrib.get(qn("w", "type"))
            if not note_id or note_type in {"separator", "continuationSeparator", "continuationNotice"}:
                continue

            paragraphs: list[str] = []
            for paragraph in note.findall("w:p", NS):
                text = self._runs_to_markdown(docx, paragraph).strip()
                text = self._clean_inline_markdown(text)
                if text:
                    paragraphs.append(text)
            if paragraphs:
                notes[note_id] = " ".join(paragraphs)
        return notes

    def _paragraph_to_markdown(self, docx: zipfile.ZipFile, paragraph: ET.Element) -> str:
        text = self._runs_to_markdown(docx, paragraph)
        if not text.strip():
            return ""
        text = self._clean_inline_markdown(text)

        style = self._paragraph_style(paragraph)
        if style:
            heading_level = self._heading_level(style)
            if heading_level:
                return f"{'#' * heading_level} {self._title_case_indonesian(text.strip())}"
            if style.lower() in {"title", "judul"}:
                return f"# {self._title_case_indonesian(text.strip())}"

        list_info = self._list_info(paragraph)
        if list_info:
            ilvl, marker = list_info
            indent = "  " * ilvl
            return f"{indent}{marker} {text.strip()}"

        text = text.strip()
        if self._is_arabic_block(text):
            return self._arabic_fence(text)
        return text

    def _paragraph_style(self, paragraph: ET.Element) -> str | None:
        style = paragraph.find("w:pPr/w:pStyle", NS)
        if style is None:
            return None
        return style.attrib.get(qn("w", "val"))

    def _heading_level(self, style: str) -> int | None:
        normalized = style.lower().replace(" ", "")
        match = re.match(r"heading([1-6])$", normalized) or re.match(r"judul([1-6])$", normalized)
        if not match:
            return None
        return int(match.group(1))

    def _list_info(self, paragraph: ET.Element) -> tuple[int, str] | None:
        num_pr = paragraph.find("w:pPr/w:numPr", NS)
        if num_pr is None:
            return None

        ilvl_el = num_pr.find("w:ilvl", NS)
        num_id_el = num_pr.find("w:numId", NS)
        ilvl = ilvl_el.attrib.get(qn("w", "val"), "0") if ilvl_el is not None else "0"
        num_id = num_id_el.attrib.get(qn("w", "val")) if num_id_el is not None else None
        fmt = self.number_formats.get((num_id or "", ilvl), "bullet")
        marker = "1." if fmt in {"decimal", "decimalZero", "lowerLetter", "upperLetter"} else "-"
        return int(ilvl) if ilvl.isdigit() else 0, marker

    def _runs_to_markdown(self, docx: zipfile.ZipFile, parent: ET.Element) -> str:
        parts: list[str] = []
        for run in parent.findall("w:r", NS):
            run_text = self._run_text(docx, run)
            if not run_text:
                continue

            properties = run.find("w:rPr", NS)
            if properties is not None:
                if properties.find("w:b", NS) is not None and run_text.strip():
                    run_text = f"**{run_text}**"
                if properties.find("w:i", NS) is not None and run_text.strip():
                    run_text = f"*{run_text}*"
                if properties.find("w:strike", NS) is not None and run_text.strip():
                    run_text = f"~~{run_text}~~"

            parts.append(run_text)
        return "".join(parts)

    def _run_text(self, docx: zipfile.ZipFile, run: ET.Element) -> str:
        parts: list[str] = []
        for child in run:
            if child.tag == qn("w", "t"):
                parts.append(child.text or "")
            elif child.tag == qn("w", "tab"):
                parts.append("\t")
            elif child.tag == qn("w", "br"):
                parts.append("  \n")
            elif child.tag == qn("w", "drawing"):
                for image in self._drawing_images(child):
                    parts.append(self._image_markdown(docx, image))
            elif child.tag == qn("w", "footnoteReference"):
                note_id = child.attrib.get(qn("w", "id"))
                if note_id:
                    parts.append(self._note_reference("footnote", note_id))
            elif child.tag == qn("w", "endnoteReference"):
                note_id = child.attrib.get(qn("w", "id"))
                if note_id:
                    parts.append(self._note_reference("endnote", note_id))
        return "".join(parts)

    def _note_reference(self, note_type: str, note_id: str) -> str:
        key = (note_type, note_id)
        if key not in self.note_numbers:
            self.note_numbers[key] = len(self.note_numbers) + 1
            self.used_notes.append(key)
        return f"[^{self.note_numbers[key]}]"

    def _drawing_images(self, drawing: ET.Element) -> Iterable[ImageRef]:
        for blip in drawing.findall(".//a:blip", NS):
            rel_id = blip.attrib.get(qn("r", "embed")) or blip.attrib.get(qn("r", "link"))
            if not rel_id:
                continue
            doc_pr = drawing.find(".//wp:docPr", NS)
            alt = ""
            if doc_pr is not None:
                alt = doc_pr.attrib.get("descr") or doc_pr.attrib.get("title") or doc_pr.attrib.get("name", "")
            yield ImageRef(rel_id=rel_id, alt=alt)

    def _image_markdown(self, docx: zipfile.ZipFile, image: ImageRef) -> str:
        if image.rel_id in self.extracted_images:
            filename = self.extracted_images[image.rel_id]
        else:
            target = self.relationships.get(image.rel_id)
            if not target:
                return ""
            filename = self._extract_image(docx, target)
            self.extracted_images[image.rel_id] = filename

        alt = self._escape_alt_text(image.alt or Path(filename).stem)
        return f"![{alt}]({self.assets_link}/{filename})"

    def _extract_image(self, docx: zipfile.ZipFile, target: str) -> str:
        source_name = Path(target).name
        safe_name = self._unique_asset_name(source_name)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        with docx.open(target) as source, (self.assets_dir / safe_name).open("wb") as destination:
            shutil.copyfileobj(source, destination)
        return safe_name

    def _unique_asset_name(self, source_name: str) -> str:
        clean_name = re.sub(r"[^A-Za-z0-9._-]+", "_", source_name).strip("._") or "image"
        path = Path(clean_name)
        stem = path.stem or "image"
        suffix = path.suffix
        candidate = f"{stem}{suffix}"
        counter = 1
        while candidate in self.used_asset_names:
            candidate = f"{stem}_{counter}{suffix}"
            counter += 1
        self.used_asset_names.add(candidate)
        return candidate

    def _table_to_markdown(self, docx: zipfile.ZipFile, table: ET.Element) -> str:
        rows: list[list[str]] = []
        for row in table.findall("w:tr", NS):
            cells: list[str] = []
            for cell in row.findall("w:tc", NS):
                cell_parts: list[str] = []
                for paragraph in cell.findall("w:p", NS):
                    text = self._clean_inline_markdown(self._runs_to_markdown(docx, paragraph)).strip()
                    if text:
                        cell_parts.append(text)
                cells.append(self._escape_table_cell("<br>".join(cell_parts)))
            if cells:
                rows.append(cells)

        if not rows:
            return ""

        width = max(len(row) for row in rows)
        normalized_rows = [row + [""] * (width - len(row)) for row in rows]
        header = normalized_rows[0]
        separator = ["---"] * width
        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(separator) + " |",
        ]
        for row in normalized_rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    def _escape_table_cell(self, value: str) -> str:
        return value.replace("|", r"\|").replace("\n", "<br>")

    def _escape_alt_text(self, value: str) -> str:
        return html.escape(value.replace("[", "(").replace("]", ")"), quote=False)

    def _clean_inline_markdown(self, value: str) -> str:
        value = re.sub(r"\*{1,3}([^*\n]+?)\*{1,3}", r"\1", value)
        value = re.sub(r"~~([^~\n]+?)~~", r"\1", value)
        value = value.replace("*", "")
        value = re.sub(r"[ \t]+", " ", value)
        value = value.replace(" .", ".").replace(" ,", ",").replace(" :", ":")
        value = value.replace(" ;", ";").replace(" ?", "?").replace(" !", "!")
        value = value.replace("“ ", "“").replace(" ”", "”")
        value = value.replace("( ", "(").replace(" )", ")")
        return value.strip()

    def _is_arabic_block(self, value: str) -> bool:
        text = re.sub(r"\[\^\d+\]$", "", value).strip()
        arabic_chars = len(re.findall(r"[\u0600-\u06FF]", text))
        latin_chars = len(re.findall(r"[A-Za-z]", text))
        return arabic_chars >= 8 and arabic_chars > latin_chars

    def _arabic_fence(self, value: str) -> str:
        trailing_notes = ""
        match = re.search(r"((?:\[\^\d+\])+)$", value)
        if match:
            trailing_notes = match.group(1)
            value = value[: match.start()].rstrip()
        fenced = f"```arabic\n{value}\n```"
        return f"{fenced}\n\n{trailing_notes}" if trailing_notes else fenced

    def _prettify_blocks(self, blocks: list[str]) -> str:
        pretty: list[str] = []
        pending_number: str | None = None
        saw_main_heading = False
        ordinal_re = re.compile(
            r"^(Pertama|Kedua|Ketiga|Keempat|Kelima|Keenam|Ketujuh|Kedelapan|Kesembilan|Kesepuluh):\s+(.+?)\.?$",
            re.IGNORECASE,
        )

        for block in blocks:
            text = block.strip()
            nomor = re.fullmatch(r"Nomor\s+([0-9]+(?:\.[0-9]+)*)", text, re.IGNORECASE)
            if nomor:
                pending_number = nomor.group(1)
                continue

            if pending_number:
                if "." in pending_number:
                    heading = f"# {pending_number} — {self._title_case_indonesian(text)}"
                    pretty.append(heading)
                    saw_main_heading = True
                pending_number = None
                continue

            ordinal = ordinal_re.match(text)
            if ordinal:
                if pretty:
                    pretty.append("---")
                title = f"{ordinal.group(1).capitalize()}: {ordinal.group(2)}"
                pretty.append(f"## {self._sentence_heading(title)}")
                continue

            if text.isupper() and len(text.split()) <= 8 and not saw_main_heading:
                pretty.append(f"# {self._title_case_indonesian(text)}")
                saw_main_heading = True
                continue

            pretty.append(text)

        return "\n\n".join(pretty)

    def _title_case_indonesian(self, value: str) -> str:
        small_words = {"dan", "di", "ke", "dari", "yang", "untuk", "bagi", "dengan", "atau"}
        words = value.lower().split()
        titled: list[str] = []
        for index, word in enumerate(words):
            if index > 0 and word in small_words:
                titled.append(word)
            else:
                titled.append(word[:1].upper() + word[1:])
        return " ".join(titled)

    def _sentence_heading(self, value: str) -> str:
        value = value.strip().rstrip(".").lower()
        value = re.sub(r"(^|:\s+)([a-zâîû])", lambda m: m.group(1) + m.group(2).upper(), value)
        replacements = {
            " islam": " Islam",
            " allah": " Allah",
            " nabi": " Nabi",
            " rasul": " Rasul",
            " aisyah": " Aisyah",
            " ibnu": " Ibnu",
            " hajar": " Hajar",
            " surga": " Surga",
        }
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value

    def _notes_markdown(self) -> str:
        lines: list[str] = []
        for note_type, note_id in self.used_notes:
            note_number = self.note_numbers[(note_type, note_id)]
            source = self.footnotes if note_type == "footnote" else self.endnotes
            text = source.get(note_id, "").strip()
            lines.append(f"[^{note_number}]: {text}")
        return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a .docx file to Markdown with images.")
    parser.add_argument("input", type=Path, help="Path ke file .docx")
    parser.add_argument("-o", "--output", type=Path, help="Path output .md")
    parser.add_argument("--assets-dir", type=Path, help="Folder output untuk gambar")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    input_path = args.input.expanduser().resolve()
    if not input_path.exists():
        print(f"File tidak ditemukan: {input_path}", file=sys.stderr)
        return 1
    if input_path.suffix.lower() != ".docx":
        print("Input harus berupa file .docx", file=sys.stderr)
        return 1

    output_path = (args.output or input_path.with_suffix(".md")).expanduser().resolve()
    assets_dir = (
        args.assets_dir.expanduser().resolve()
        if args.assets_dir
        else output_path.with_name(f"{output_path.stem}_assets")
    )

    converter = DocxToMarkdown(input_path, output_path, assets_dir)
    try:
        converter.write()
    except (zipfile.BadZipFile, ET.ParseError, ValueError, KeyError) as exc:
        print(f"Gagal mengkonversi DOCX: {exc}", file=sys.stderr)
        return 1

    print(f"Markdown dibuat: {output_path}")
    print(f"Gambar disimpan di: {assets_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
