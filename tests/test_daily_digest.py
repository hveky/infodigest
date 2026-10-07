"""Synthetic-only offline acceptance and mocked delivery tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import fitz
from pypdf import PdfReader
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
FONT = Path("C:/Windows/Fonts/simhei.ttf")
DATE = "2030-01-02"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load("build_digest_pdf")
pusher = load("push_digest_pdf")


def fixtures(directory: Path):
    paths = []
    for key, title, minimum in builder.SOURCES:
        extra = "\n> 采集窗口：2030-01-01 08:00 至 2030-01-02 08:00；合成样本，非真实采集。\n"
        text = (
            f"# 合成报告为何值得验证？\n## 仅用于离线测试\n> {title} 日报 · {DATE}\n"
            + extra
        )
        if key == "telegram":
            text += "\n".join(
                f"## {section}\n{key}首段标记\n"
                + "合成正文用于检查完整保留中文分页与链接。" * (minimum // 40 + 100)
                for section in builder.TG_SECTIONS
            )
        else:
            text += (
                f"## 数据概览\n{key}首段标记\n"
                + "合成正文用于检查完整保留中文分页与链接。" * (minimum // 20 + 200)
            )
        text += f"\n\n{key}中间标记\n\n> 引文测试。\n\n- 列表甲\n- 列表乙\n\n[原文](https://example.invalid/{key})\n\n"
        text += "3. numbered first\n4. numbered second\n    - nested child\n\n"
        text += (
            "```text\nCODESTART\n    indented  repeated\n"
            + "long code line 0123456789 " * 600
            + "\nCODEEND\n```\n\n"
        )
        text += (
            "| 列甲 | 列乙 |\n| --- | --- |\n| TABLESTART | "
            + "长单元格内容" * 500
            + " TABLEEND |\n\n"
        )
        text += "<script>DO_NOT_EXECUTE_OR_RENDER</script>\n\n![图片替代](https://example.invalid/no-download.png)\n\n"
        text += (
            "https://example.invalid/" + "longsegment" * 100 + f"\n\n{key}末段标记\n"
        )
        path = directory / f"{key}.md"
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


class DigestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.paths = fixtures(cls.directory)
        cls.pdf = builder.build(DATE, cls.paths, cls.directory / "combined", FONT)
        cls.manifest = cls.pdf.with_suffix(".manifest.json")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_pdf_full_text_structure_fonts_links_and_pages(self):
        reader = PdfReader(self.pdf)
        text = "".join(p.extract_text() for p in reader.pages)
        self.assertGreater(len(reader.pages), 15)
        self.assertEqual(len(reader.outline), 3)
        for key, _, _ in builder.SOURCES:
            for marker in ("首段标记", "中间标记", "末段标记"):
                self.assertIn(key + marker, text)
        for marker in ("CODESTART", "CODEEND", "TABLESTART", "TABLEEND", "图片替代"):
            self.assertIn(marker, text)
        self.assertNotIn("DO_NOT_EXECUTE_OR_RENDER", text)
        self.assertIn("3. numbered first", text)
        self.assertIn("4. numbered second", text)
        self.assertIn("    indented  repeated", text)
        self.assertIn("nested child", text)
        self.assertIn("目录", reader.pages[1].extract_text())
        chapter_pages = [
            reader.get_destination_page_number(entry) + 1 for entry in reader.outline
        ]
        toc = reader.pages[1].extract_text()
        import re

        for entry, page in zip(reader.outline, chapter_pages):
            self.assertRegex(toc, r"\b" + str(page) + r"\s*" + re.escape(entry.title))
        with fitz.open(self.pdf) as full_doc:
            # Exclude footer region before joining page-spanning prose/code.
            normalized = re.sub(
                r"\s+",
                "",
                "".join(
                    p.get_text(clip=fitz.Rect(0, 0, p.rect.width, p.rect.height - 35))
                    for p in full_doc
                ),
            )
        originals = "".join(p.read_text(encoding="utf-8") for p in self.paths)
        for phrase in (
            "合成正文用于检查完整保留中文分页与链接。",
            "长单元格内容",
            "longcodeline0123456789",
        ):
            expected = re.sub(r"\s+", "", originals).count(phrase)
            self.assertEqual(normalized.count(phrase), expected)
        self.assertTrue(any(p.get("/Annots") for p in reader.pages))
        fonts = [
            f.get_object()
            for p in reader.pages
            for f in p["/Resources"]["/Font"].get_object().values()
        ]
        self.assertTrue(
            any(
                (
                    "/FontFile2" in f.get("/FontDescriptor", {}).get_object()
                    if hasattr(f.get("/FontDescriptor", {}), "get_object")
                    else False
                )
                for f in fonts
            )
        )
        with fitz.open(self.pdf) as doc:
            self.assertEqual(len(doc.get_toc()), 3)
            self.assertTrue(any(page.get_links() for page in doc))
            for index, page in enumerate(doc):
                footer = page.get_text(
                    clip=fitz.Rect(
                        0, page.rect.height - 35, page.rect.width, page.rect.height
                    )
                ).strip()
                self.assertEqual(footer, str(index + 1))
                self.assertAlmostEqual(page.rect.width, 595.28, delta=1)

    def test_no_clobber_resume_hashes_deterministic(self):
        before = self.pdf.read_bytes()
        other = builder.build(DATE, self.paths, self.pdf.parent, FONT)
        self.assertNotEqual(other, self.pdf)
        self.assertEqual(other.read_bytes(), before)
        self.assertEqual(
            builder.build(DATE, self.paths, self.pdf.parent, FONT, self.manifest),
            self.pdf,
        )
        with tempfile.TemporaryDirectory() as name:
            paths = fixtures(Path(name))
            paths[0].write_text(
                paths[0].read_text(encoding="utf-8") + "变化", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "mismatch"):
                builder.build(DATE, paths, Path(name) / "out", FONT, self.manifest)

    def test_invalid_inputs_leave_no_pdf(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            paths = fixtures(directory)
            original = paths[0].read_text(encoding="utf-8")
            for invalid in (
                "",
                "# 空报告\n> 日期 2030-01-02\n不足",
                original.replace(DATE, "2030-01-03", 1),
            ):
                paths[0].write_text(invalid, encoding="utf-8")
                with self.assertRaises(ValueError):
                    builder.build(DATE, paths, directory / "out", FONT)
            paths[0].write_text(original, encoding="utf-8")
            with self.assertRaises(ValueError):
                builder.build(DATE, paths, directory / "out", directory / "missing.ttf")
            with self.assertRaises(FileNotFoundError):
                builder.build(
                    DATE,
                    [directory / "missing.md", *paths[1:]],
                    directory / "out",
                    FONT,
                )
            self.assertFalse(list((directory / "out").glob("*.pdf")))

    def test_metadata_conflicting_body_and_fenced_dates_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            paths = fixtures(Path(name))
            original = paths[0].read_text(encoding="utf-8")
            for bad in (
                original.replace(
                    f"> Linux.do 日报 · {DATE}",
                    f"> Linux.do 日报 · {DATE}\n> 报告日期：2030-01-03",
                ),
                original.replace(f"> Linux.do 日报 · {DATE}", "")
                + f"\n> 日报日期 {DATE}",
                original.replace(
                    f"> Linux.do 日报 · {DATE}", f"```text\n> 日报日期 {DATE}\n```"
                ),
            ):
                paths[0].write_text(bad, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "header date"):
                    builder.validate_sources(DATE, paths)

    def test_code_cannot_satisfy_article_quality_or_sections(self):
        with tempfile.TemporaryDirectory() as name:
            paths = fixtures(Path(name))
            original = paths[0].read_text(encoding="utf-8")
            for code in (
                "~~~text\n" + "汉" * 7000 + "\n~~~",
                "````text\n" + "汉" * 7000 + "\n````",
                "    " + "汉" * 7000,
                "`" + "汉" * 7000 + "`",
            ):
                paths[0].write_text(
                    f"# 测试\n> Linux.do 日报 · {DATE}\n\n" + code, encoding="utf-8"
                )
                with self.assertRaisesRegex(ValueError, "Chinese body"):
                    builder.validate_sources(DATE, paths)
            paths[0].write_text(original, encoding="utf-8")
            tg = paths[2].read_text(encoding="utf-8")
            for fence in ("```", "~~~", "````"):
                bad = tg
                for section in builder.TG_SECTIONS:
                    bad = bad.replace("## " + section, "## 非规定标题")
                bad += (
                    "\n\n"
                    + fence
                    + "\n"
                    + "\n".join("## " + s for s in builder.TG_SECTIONS)
                    + "\n"
                    + fence
                )
                paths[2].write_text(bad, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "four"):
                    builder.validate_sources(DATE, paths)

    def test_manifest_failure_leaves_no_published_pair(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            paths = fixtures(directory)
            with patch.object(
                builder.json, "dump", side_effect=OSError("synthetic disk failure")
            ):
                with self.assertRaises(OSError):
                    builder.build(DATE, paths, directory / "out", FONT)
            self.assertEqual(list((directory / "out").iterdir()), [])

    def test_tg_four_sections_required_and_heading_levels(self):
        with tempfile.TemporaryDirectory() as name:
            paths = fixtures(Path(name))
            original = paths[2].read_text(encoding="utf-8")
            paths[2].write_text(
                original.replace("## 总论", "### 总论"), encoding="utf-8"
            )
            builder.validate_sources(DATE, paths)
            paths[2].write_text(
                original.replace("## 总结", "## 缺少总结"), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "four"):
                builder.validate_sources(DATE, paths)

    def test_skill_sync_frontmatter_and_routes(self):
        for name in (
            "daily-digest",
            "forum-research",
            "xiaoheihe-daily-digest",
            "channel-digest",
        ):
            text = (ROOT / ".claude/skills" / name / "SKILL.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                text,
                (ROOT / ".agents/skills" / name / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(yaml.safe_load(text.split("---", 2)[1])["name"], name)
            self.assertNotIn("web-access:web-access", text)
            self.assertRegex(text, r"禁止(?:任何)?单份推送")
        daily = (ROOT / ".claude/skills/daily-digest/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("跑一下今天的", daily)
        self.assertIn("明确点名单一来源优先", daily)

    def test_import_no_io_or_network(self):
        with patch("pathlib.Path.read_text", side_effect=AssertionError("I/O")), patch(
            "requests.post", side_effect=AssertionError("network")
        ):
            load("build_digest_pdf")
            load("push_digest_pdf")


class PushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.paths = fixtures(self.directory)
        self.pdf = builder.build(DATE, self.paths, self.directory / "out", FONT)
        self.manifest = self.pdf.with_suffix(".manifest.json")
        self.env = self.directory / "fake.env"
        self.env.write_text(
            "BOT_TOKEN=12345:SYNTHETIC_TOKEN\nBOT_CHAT_ID=987654\n", encoding="utf-8"
        )
        self.config = self.directory / "config.yaml"
        self.set_config(True)
        self.post = Mock(
            return_value=Mock(status_code=200, json=Mock(return_value={"ok": True}))
        )

    def tearDown(self):
        self.temp.cleanup()

    def set_config(self, enabled):
        self.config.write_text(
            yaml.safe_dump(
                {
                    "channel_digest": {
                        "push": {"enabled": enabled, "caption_with_title": False},
                        "auth": {"env_file": str(self.env)},
                    }
                }
            ),
            encoding="utf-8",
        )

    def send(self, **kwargs):
        return pusher.push(
            self.pdf, self.manifest, self.config, post=self.post, **kwargs
        )

    def test_once_and_explicit_resend(self):
        self.assertEqual(self.send(), "sent")
        self.assertEqual(self.send(), "already_sent")
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(
            self.post.call_args.kwargs["files"]["document"][2], "application/pdf"
        )
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])
        self.assertEqual(self.send(resend=True), "sent")
        self.assertEqual(self.post.call_count, 2)
        self.assertNotIn(
            "SYNTHETIC_TOKEN", self.pdf.with_suffix(".push.json").read_text()
        )

    def test_disabled_never_reads_credentials(self):
        self.set_config(False)
        self.env.unlink()
        self.assertEqual(self.send(), "disabled")
        self.post.assert_not_called()

    def test_timeout_unknown_blocks_retry(self):
        self.post.side_effect = requests.Timeout(
            "https://api.telegram.org/bot12345:SYNTHETIC_TOKEN/private"
        )
        with self.assertRaises(pusher.PushError) as caught:
            self.send()
        self.assertNotIn("SYNTHETIC_TOKEN", str(caught.exception))
        self.assertEqual(
            json.loads(self.pdf.with_suffix(".push.json").read_text())["status"],
            "unknown",
        )
        with self.assertRaises(pusher.PushError):
            self.send()
        with self.assertRaises(pusher.PushError):
            self.send(resend=True)
        self.assertEqual(self.post.call_count, 1)
        self.post.side_effect = None
        self.assertEqual(self.send(resend=True, confirm_unknown=True), "sent")
        self.assertTrue(self.pdf.exists())

    def test_http_failure_and_ok_false_redacted(self):
        for status, payload in (
            (400, {"ok": False}),
            (200, {"ok": False, "description": "SYNTHETIC_TOKEN"}),
        ):
            self.post.return_value = Mock(
                status_code=status, json=Mock(return_value=payload)
            )
            with self.assertRaises(pusher.PushError) as caught:
                self.send()
            self.assertNotIn("SYNTHETIC_TOKEN", str(caught.exception))
            self.assertTrue(self.pdf.exists())
            self.assertEqual(
                json.loads(self.pdf.with_suffix(".push.json").read_text())["status"],
                "failed",
            )

    def test_upload_uses_validated_snapshot(self):
        before = self.pdf.read_bytes()
        captured = []

        def fake_post(*args, **kwargs):
            self.pdf.write_bytes(b"%PDF-mutated")
            captured.append(kwargs["files"]["document"][1].read())
            return Mock(status_code=200, json=Mock(return_value={"ok": True}))

        self.post.side_effect = fake_post
        self.assertEqual(self.send(), "sent")
        self.assertEqual(captured, [before])
        self.assertEqual(
            json.loads(self.pdf.with_suffix(".push.json").read_text())["pdf_sha256"],
            pusher.digest(before),
        )

    def test_invalid_configuration_sanitized_before_network(self):
        for channel in (
            {"push": []},
            {"push": {"enabled": True}},
            {"push": {"enabled": True}, "auth": {"env_file": []}},
            {
                "push": {"enabled": True, "caption_with_title": "yes"},
                "auth": {"env_file": str(self.env)},
            },
        ):
            self.config.write_text(
                yaml.safe_dump({"channel_digest": channel}), encoding="utf-8"
            )
            with self.assertRaises(pusher.PushError):
                self.send()
        self.post.assert_not_called()

    def test_unknown_server_json_interrupt_and_success_persistence(self):
        for outcome in ("server", "json", "interrupt", "persist"):
            self.pdf.with_suffix(".push.json").unlink(missing_ok=True)
            self.post.reset_mock(side_effect=True)
            self.post.return_value = Mock(
                status_code=200, json=Mock(return_value={"ok": True})
            )
            if outcome == "server":
                self.post.return_value.status_code = 503
            elif outcome == "json":
                self.post.return_value.json.side_effect = ValueError("SYNTHETIC_TOKEN")
            elif outcome == "interrupt":
                self.post.side_effect = KeyboardInterrupt()
            actual_write = pusher.write_state

            def write(path, state):
                if outcome == "persist" and state["status"] == "sent":
                    raise OSError("SYNTHETIC_TOKEN")
                actual_write(path, state)

            with patch.object(pusher, "write_state", side_effect=write):
                with self.assertRaises((pusher.PushError, KeyboardInterrupt)) as caught:
                    self.send()
            self.assertNotIn("SYNTHETIC_TOKEN", str(caught.exception))
            self.assertEqual(
                json.loads(self.pdf.with_suffix(".push.json").read_text())["status"],
                "unknown",
            )
            with self.assertRaises(pusher.PushError):
                self.send()
            self.assertEqual(self.post.call_count, 1)

    def test_lock_and_initial_state_failure_no_send(self):
        lock = self.pdf.with_suffix(".push.lock")
        lock.touch()
        with self.assertRaises(pusher.PushError):
            self.send()
        lock.unlink()
        with patch.object(
            pusher, "write_state", side_effect=OSError("SYNTHETIC_TOKEN")
        ):
            with self.assertRaises(pusher.PushError) as caught:
                self.send()
        self.assertNotIn("SYNTHETIC_TOKEN", str(caught.exception))
        self.post.assert_not_called()

    def test_hash_change_and_lock_block_before_credentials(self):
        self.paths[0].write_text("changed", encoding="utf-8")
        with self.assertRaises(pusher.PushError):
            self.send()
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
