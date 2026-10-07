"""Offline, explicit-source daily digest PDF builder. No import-time I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import date
from html import escape
from html.parser import HTMLParser
from pathlib import Path

import markdown
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    PageBreak,
    Spacer,
)
from reportlab.platypus.tableofcontents import TableOfContents

SOURCES = (
    ("linuxdo", "Linux.do", 6000),
    ("xiaoheihe", "小黑盒", 10000),
    ("telegram", "Telegram", 6000),
)
TG_SECTIONS = ["总论", "信息内容", "传播机制", "总结"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_sources(report_date: str, paths: list[Path]) -> list[dict]:
    if date.fromisoformat(report_date).isoformat() != report_date:
        raise ValueError("date must be YYYY-MM-DD")
    if len(paths) != 3 or len({p.resolve() for p in paths}) != 3:
        raise ValueError("three distinct explicit source paths required")
    result = []
    for (key, title, minimum), path in zip(SOURCES, paths):
        if path.suffix.lower() != ".md":
            raise ValueError(f"{key}: Markdown required")
        data = path.read_bytes()
        text = data.decode("utf-8-sig")
        if not text.strip() or not re.search(r"^#\s+\S", text, re.M):
            raise ValueError(f"{key}: empty or invalid Markdown report")
        # Metadata is the first contiguous quote block before the article body.
        # Fenced examples/body quotations cannot act as identity metadata.
        lines = text.splitlines(keepends=True)
        offset = 0
        metadata = []
        for line in lines:
            stripped = line.strip()
            if not metadata:
                if stripped.startswith(("```", "~~~")):
                    break
                if stripped.startswith(">"):
                    metadata.append(line)
                elif stripped and not stripped.startswith("#"):
                    break
            elif stripped.startswith(">") or not stripped:
                metadata.append(line)
            else:
                break
            offset += len(line)
        identity_headers = [
            h
            for h in metadata
            if re.search(r"报告|日报|日期", h)
            and not re.search(r"窗口|since|until", h, re.I)
        ]
        identity_dates = [
            d for h in identity_headers for d in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", h)
        ]
        if not identity_dates or any(d != report_date for d in identity_dates):
            raise ValueError(
                f"{key}: report header date mismatch or ambiguous identity"
            )
        # Count prose after metadata; exclude URLs, code and headings.
        body = text[offset:]
        article_html = markdown.markdown(
            body, extensions=["tables", "fenced_code", "sane_lists"]
        )
        quality = QualityBlocks()
        quality.feed(article_html)
        prose = re.sub(r"https?://\S+", "", "".join(quality.prose))
        count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", prose))
        if count < minimum:
            raise ValueError(f"{key}: Chinese body characters {count} < {minimum}")
        if key == "telegram":
            sections = [h for h in quality.headings if h in TG_SECTIONS]
            if sections != TG_SECTIONS:
                raise ValueError("telegram: exactly four ordered sections required")
        result.append(
            dict(
                source=key,
                title=title,
                path=str(path.resolve()),
                date=report_date,
                chinese_chars=count,
                sha256=sha256(data),
                text=text,
            )
        )
    return result


class QualityBlocks(HTMLParser):
    """Article quality gate over parsed Markdown, never code or headings as prose."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.prose = []
        self.headings = []
        self.heading = None
        self.excluded = []

    def handle_starttag(self, tag, attrs):
        if tag in {"pre", "code", "script", "style"}:
            self.excluded.append(tag)
        if not self.excluded and re.fullmatch(r"h[1-6]", tag):
            self.heading = []

    def handle_endtag(self, tag):
        if self.excluded:
            if self.excluded[-1] == tag:
                self.excluded.pop()
            return
        if self.heading is not None and re.fullmatch(r"h[1-6]", tag):
            self.headings.append("".join(self.heading).strip())
            self.heading = None

    def handle_data(self, data):
        if self.excluded:
            return
        if self.heading is not None:
            self.heading.append(data)
        else:
            self.prose.append(data)


class TextBlocks(HTMLParser):
    """Convert Markdown HTML into safe textual blocks; never fetch assets."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.parts = []
        self.links = []
        self.heading = False
        self.ignored = 0
        self.pre = False
        self.lists = []

    def flush(self):
        if self.parts:
            self.blocks.append(("".join(self.parts), self.heading))
            self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored:
            return
        attrs = dict(attrs)
        if tag in {"ol", "ul"}:
            self.flush()
            self.lists.append([tag, int(attrs.get("start", "1"))])
        if tag == "pre":
            self.pre = True
        if tag in {
            "p",
            "blockquote",
            "li",
            "pre",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }:
            self.flush()
            self.heading = tag.startswith("h") and tag[1:].isdigit()
        if tag == "li":
            prefix = "•"
            if self.lists and self.lists[-1][0] == "ol":
                if "value" in attrs:
                    self.lists[-1][1] = int(attrs["value"])
                prefix = f"{self.lists[-1][1]}."
                self.lists[-1][1] += 1
            self.parts.append(
                "&nbsp;" * (4 * max(0, len(self.lists) - 1)) + prefix + " "
            )
        if tag == "br":
            self.parts.append("\n")
        if tag == "a":
            url = attrs.get("href", "")
            if url.startswith(("https://", "http://")):
                self.links.append(url)
                self.parts.append(f'<link href="{escape(url, quote=True)}">')
            else:
                self.links.append(None)
        if tag == "img":
            self.parts.append(
                escape(f"[图片: {attrs.get('alt', '')}] {attrs.get('src', '')}")
            )

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.ignored:
            self.ignored -= 1
            return
        if self.ignored:
            return
        if tag in {"ol", "ul"}:
            self.flush()
            if self.lists:
                self.lists.pop()
        if tag == "pre":
            self.pre = False
        if tag == "a" and self.links:
            url = self.links.pop()
            if url:
                self.parts.append("</link> " + escape(url))
        if tag in {"td", "th"}:
            self.parts.append(" | ")
        if tag in {"p", "li", "pre", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.flush()
            self.heading = False

    def handle_data(self, data):
        if self.ignored:
            return
        if not self.pre and not data.strip():
            return
        safe = escape(data.expandtabs(4))
        if self.pre:
            safe = safe.replace(" ", "&nbsp;")
        self.parts.append(safe.replace("\n", "<br/>"))


class DigestDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if hasattr(flowable, "chapter_key"):
            key, title = flowable.chapter_key
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, 0)
            self.notify("TOCEntry", (0, title, self.page, key))


def footer(canvas, doc):
    canvas.setFont("DigestChinese", 9)
    canvas.drawCentredString(A4[0] / 2, 22, str(doc.page))


def render_pdf(path: Path, report_date: str, sources: list[dict], font: Path):
    if not font.is_file():
        raise ValueError("Chinese font not found; provide --font")
    pdfmetrics.registerFont(TTFont("DigestChinese", str(font)))
    body = ParagraphStyle(
        "Body",
        fontName="DigestChinese",
        fontSize=10,
        leading=16,
        wordWrap="CJK",
        spaceAfter=7,
        splitLongWords=True,
    )
    heading = ParagraphStyle(
        "Heading", parent=body, fontSize=15, leading=22, spaceAfter=12
    )
    doc = DigestDoc(
        str(path),
        pagesize=A4,
        leftMargin=45,
        rightMargin=45,
        topMargin=45,
        bottomMargin=40,
        invariant=1,
    )
    doc.addPageTemplates(
        PageTemplate(
            id="body",
            frames=[Frame(45, 40, A4[0] - 90, A4[1] - 85, id="normal")],
            onPage=footer,
        )
    )
    story = [
        Spacer(1, 100),
        Paragraph("信息聚合日报", heading),
        Paragraph(report_date, heading),
        Paragraph("三份全文合刊 · Linux.do / 小黑盒 / Telegram", body),
        Paragraph("各来源采集窗口与样本限制以章节原文为准；不跨来源重写或删减。", body),
        PageBreak(),
        Paragraph("目录", heading),
    ]
    toc = TableOfContents()
    toc.levelStyles = [body]
    story.extend([toc, PageBreak()])
    for index, source in enumerate(sources):
        if index:
            story.append(PageBreak())
        title = f"第{index+1}章 · {source['title']}"
        chapter = Paragraph(title, heading)
        chapter.chapter_key = (f"chapter-{index}", title)
        story.append(chapter)
        parser = TextBlocks()
        parser.feed(
            markdown.markdown(
                source["text"], extensions=["tables", "fenced_code", "sane_lists"]
            )
        )
        parser.flush()
        for text, is_heading in parser.blocks:
            story.append(Paragraph(text, heading if is_heading else body))
    doc.multiBuild(story, canvasmaker=Canvas)


def build(
    report_date: str,
    paths: list[Path],
    output_dir: Path,
    font: Path,
    resume: Path | None = None,
) -> Path:
    sources = validate_sources(report_date, paths)
    clean = [{k: v for k, v in s.items() if k != "text"} for s in sources]
    if resume:
        previous = json.loads(resume.read_text(encoding="utf-8"))
        if previous.get("date") != report_date or previous.get("sources") != clean:
            raise ValueError("resume manifest source/date/hash mismatch")
        pdf = Path(previous["pdf"])
        if not pdf.is_file() or sha256(pdf.read_bytes()) != previous["pdf_sha256"]:
            raise ValueError("resume PDF hash mismatch")
        return pdf
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(suffix=".pdf.tmp", dir=output_dir)
    os.close(fd)
    temp_path = Path(temp)
    try:
        render_pdf(temp_path, report_date, sources, font)
        from pypdf import PdfReader

        reader = PdfReader(temp_path)
        if len(reader.pages) < 5 or len(reader.outline) != 3:
            raise ValueError("PDF validation failed")
        index = 1
        while True:
            suffix = "" if index == 1 else f"_{index}"
            pdf = output_dir / f"{report_date}{suffix}.pdf"
            manifest = pdf.with_suffix(".manifest.json")
            lock = pdf.with_suffix(".build.lock")
            try:
                lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                index += 1
                continue
            os.close(lock_fd)
            manifest_temp = None
            owned_pdf = False
            try:
                if pdf.exists() or manifest.exists():
                    index += 1
                    continue
                payload = dict(
                    version=1,
                    date=report_date,
                    sources=clean,
                    pdf=str(pdf.resolve()),
                    pdf_sha256=sha256(temp_path.read_bytes()),
                )
                fd, name = tempfile.mkstemp(suffix=".manifest.tmp", dir=output_dir)
                manifest_temp = Path(name)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temp_path, pdf)
                owned_pdf = True
                os.link(manifest_temp, manifest)
                return pdf
            except BaseException:
                # Remove only our newly linked PDF, never a pre-existing artifact.
                if owned_pdf:
                    pdf.unlink(missing_ok=True)
                raise
            finally:
                if manifest_temp:
                    manifest_temp.unlink(missing_ok=True)
                lock.unlink(missing_ok=True)
    finally:
        temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    for key, _, _ in SOURCES:
        parser.add_argument(f"--{key}", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--font", type=Path, default=Path("C:/Windows/Fonts/simhei.ttf")
    )
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    try:
        print(
            build(
                args.date,
                [args.linuxdo, args.xiaoheihe, args.telegram],
                args.output_dir,
                args.font,
                args.resume,
            )
        )
    except (ValueError, OSError, UnicodeError) as exc:
        parser.exit(1, f"Build failed: {exc}\n")


if __name__ == "__main__":
    main()
