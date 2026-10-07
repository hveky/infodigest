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
        self.kind = "body"
        self.quote_depth = 0
        self.ignored = 0
        self.pre = False
        self.lists = []

    def flush(self):
        if self.parts:
            self.blocks.append(("".join(self.parts), self.kind))
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
            if tag == "blockquote":
                self.quote_depth += 1
            self.kind = (
                tag
                if self.heading
                else (
                    "code"
                    if tag == "pre"
                    else (
                        "table"
                        if tag == "tr"
                        else (
                            "list"
                            if tag == "li"
                            else "quote" if self.quote_depth else "body"
                        )
                    )
                )
            )
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
        if tag == "blockquote":
            self.flush()
            self.quote_depth = max(0, self.quote_depth - 1)
        if tag == "pre":
            self.pre = False
        if tag == "a" and self.links:
            url = self.links.pop()
            if url:
                # The original link label is retained, the full target lives in
                # the PDF annotation rather than being duplicated into prose.
                self.parts.append("</link>")
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


class VisibleText(HTMLParser):
    """Extract visible labels from the safe ReportLab paragraph markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []

    def handle_data(self, data):
        self.text.append(data)


INK = colors.HexColor("#242b2a")
MUTED = colors.HexColor("#68716e")
ACCENT = colors.HexColor("#286657")
RULE = colors.HexColor("#d5ded9")
PAPER = colors.HexColor("#f4f5f1")


class DigestDoc(BaseDocTemplate):
    def beforeDocument(self):
        self.current_source = "阅读目录"

    def afterFlowable(self, flowable):
        if hasattr(flowable, "chapter_key"):
            key, title = flowable.chapter_key
            self.current_source = title
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, 0)
            self.notify("TOCEntry", (0, title, self.page, key))


def footer(canvas, doc):
    canvas.saveState()
    if doc.page == 1:
        canvas.setFillColor(PAPER)
        canvas.rect(0, 0, *A4, fill=1, stroke=0)
        canvas.setFillColor(ACCENT)
        canvas.rect(54, A4[1] - 110, 46, 4, fill=1, stroke=0)
    else:
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(54, A4[1] - 45, A4[0] - 54, A4[1] - 45)
        canvas.line(54, 43, A4[0] - 54, 43)
        canvas.setFont("DigestChinese", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(54, A4[1] - 33, "信息聚合日报  /  " + doc.report_date)
        canvas.drawString(54, 28, "三份全文合刊 · 采集限制以各章原文为准")
    # Keep the page number isolated for machine-verifiable pagination.
    canvas.setFont("DigestChinese", 9)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(A4[0] / 2, 17, str(doc.page))
    canvas.restoreState()


def register_fonts(font: Path, sources: list[dict]):
    """Use only local TrueType outlines; verify actual cmap coverage before build."""
    if not font.is_file():
        raise ValueError("Chinese font not found; provide --font")
    heading_font = TTFont("DigestChinese", str(font))
    pdfmetrics.registerFont(heading_font)
    # Windows' SimSun collection contains embeddable TrueType outlines. A custom
    # font elsewhere remains self-contained rather than unexpectedly selecting it.
    song = font.parent / "simsun.ttc"
    body_font = (
        TTFont("DigestBody", str(song), subfontIndex=0)
        if song.is_file()
        else TTFont("DigestBody", str(font))
    )
    # Check rendered text, not raw Markdown URLs, scripts or comments. Every
    # visible Unicode scalar matters (including non-BMP CJK and symbols).
    for source in sources:
        parser = TextBlocks()
        parser.feed(
            markdown.markdown(
                source["text"], extensions=["tables", "fenced_code", "sane_lists"]
            )
        )
        parser.flush()
        for text, kind in parser.blocks:
            visible = VisibleText()
            visible.feed(text)
            candidate = (
                heading_font if kind.startswith("h") or kind == "code" else body_font
            )
            missing = {
                ord(c)
                for c in "".join(visible.text)
                if not c.isspace() and ord(c) not in candidate.face.charToGlyph
            }
            if missing:
                raise ValueError(
                    "local font missing visible glyphs: "
                    + " ".join(f"U+{c:04X}" for c in sorted(missing)[:12])
                )
    pdfmetrics.registerFont(body_font)


def render_pdf(path: Path, report_date: str, sources: list[dict], font: Path):
    register_fonts(font, sources)
    body = ParagraphStyle(
        "Body",
        fontName="DigestBody",
        fontSize=10.5,
        leading=18.5,
        wordWrap="CJK",
        spaceAfter=9,
        textColor=INK,
        splitLongWords=True,
        allowWidows=0,
        allowOrphans=0,
    )
    heading = ParagraphStyle(
        "Heading",
        parent=body,
        fontName="DigestChinese",
        fontSize=22,
        leading=31,
        spaceBefore=17,
        spaceAfter=15,
        keepWithNext=True,
    )
    styles = {"body": body}
    for level, size, leading in (
        (1, 19, 28),
        (2, 15, 23),
        (3, 12, 20),
        (4, 11, 19),
        (5, 10.5, 19),
        (6, 10.5, 19),
    ):
        styles[f"h{level}"] = ParagraphStyle(
            f"H{level}",
            parent=heading,
            fontSize=size,
            leading=leading,
            spaceBefore=19 if level < 3 else 13,
            spaceAfter=10 if level < 3 else 7,
            textColor=ACCENT if level == 2 else INK,
        )
    styles["quote"] = ParagraphStyle(
        "Quote",
        parent=body,
        fontSize=9.5,
        leading=16.5,
        textColor=MUTED,
        leftIndent=13,
        rightIndent=9,
        borderColor=RULE,
        borderWidth=0.5,
        borderPadding=9,
        backColor=PAPER,
        spaceBefore=6,
        spaceAfter=12,
    )
    styles["list"] = ParagraphStyle("List", parent=body, leftIndent=12, spaceAfter=5)
    styles["table"] = ParagraphStyle(
        "TableRow",
        parent=body,
        fontSize=9,
        leading=15,
        backColor=PAPER,
        borderColor=RULE,
        borderWidth=0.4,
        borderPadding=6,
        leftIndent=7,
        rightIndent=7,
        spaceAfter=5,
    )
    styles["code"] = ParagraphStyle(
        "Code",
        parent=body,
        fontName="DigestChinese",
        fontSize=8.2,
        leading=12.5,
        backColor=PAPER,
        borderPadding=8,
        leftIndent=9,
        rightIndent=9,
        spaceBefore=5,
        spaceAfter=12,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontName="DigestChinese",
        fontSize=9,
        leading=16,
        textColor=MUTED,
    )
    cover = ParagraphStyle(
        "Cover", parent=heading, fontSize=34, leading=48, spaceAfter=20
    )
    doc = DigestDoc(
        str(path),
        pagesize=A4,
        leftMargin=54,
        rightMargin=54,
        topMargin=64,
        bottomMargin=57,
        invariant=1,
        title=f"信息聚合日报 · {report_date}",
        author="",
    )
    doc.report_date = report_date
    doc.addPageTemplates(
        PageTemplate(
            id="body",
            frames=[
                Frame(
                    54,
                    57,
                    A4[0] - 108,
                    A4[1] - 121,
                    id="normal",
                    leftPadding=0,
                    rightPadding=0,
                )
            ],
            onPage=footer,
            onPageEnd=source_header,
        )
    )
    story = [
        Spacer(1, 74),
        Paragraph("DAILY READING  /  全文合刊", small),
        Spacer(1, 28),
        Paragraph("信息聚合<br/>日报", cover),
        Paragraph(report_date.replace("-", " / "), styles["h2"]),
        Spacer(1, 32),
        Paragraph("01　Linux.do<br/>02　小黑盒<br/>03　Telegram", body),
        Spacer(1, 88),
        Paragraph("三份独立观察，一册完整阅读。", styles["h3"]),
        Paragraph(
            "各来源采集窗口与样本限制以章节原文为准；不跨来源重写或删减。", small
        ),
        PageBreak(),
        Paragraph("阅读目录", heading),
        Paragraph("按来源保留全文 · 点击章节或使用 PDF 书签跳转", small),
        Spacer(1, 24),
    ]
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "TOC",
            parent=body,
            fontName="DigestChinese",
            fontSize=13,
            leading=23,
            spaceBefore=18,
            spaceAfter=18,
        )
    ]
    story.extend(
        [
            toc,
            Spacer(1, 32),
            Paragraph("阅读说明", styles["h3"]),
            Paragraph(
                "三个章节各自保留原文结构与链接。网页智能排序、平台热榜和频道窗口并非统一样本；请结合原文的数据说明阅读。链接文字保持原样，完整目标地址保留在可点击注释中；原文裸网址不删减。",
                small,
            ),
            PageBreak(),
        ]
    )
    for index, source in enumerate(sources):
        if index:
            story.append(PageBreak())
        title = f"第{index+1}章 · {source['title']}"
        chapter = Paragraph(title, heading)
        chapter.chapter_key = (f"chapter-{index}", title)
        story.extend([Paragraph(f"SOURCE 0{index + 1}  /  独立全文", small), chapter])
        parser = TextBlocks()
        parser.feed(
            markdown.markdown(
                source["text"], extensions=["tables", "fenced_code", "sane_lists"]
            )
        )
        parser.flush()
        for text, kind in parser.blocks:
            story.append(Paragraph(text, styles[kind]))
    doc.multiBuild(story, canvasmaker=Canvas)


def source_header(canvas, doc):
    """Write source after layout so a chapter's first page has its own header."""
    if doc.page <= 1:
        return
    canvas.saveState()
    canvas.setFillColor(colors.white)
    canvas.rect(A4[0] / 2 + 10, A4[1] - 42, A4[0] / 2 - 64, 15, fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.setFont("DigestChinese", 8)
    canvas.drawRightString(A4[0] - 54, A4[1] - 33, doc.current_source)
    canvas.restoreState()


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
