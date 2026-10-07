<div align="center">

# 📡 InfoDigest

### Your personal intelligence officer — turn a day of forums, communities, and channels into one report

Automatically reads the past 24 hours across **the LinuxDo forum**, **Heybox (小黑盒) community**, and **Telegram channels**,
then classifies, scores, and synthesizes it into one opinionated daily digest.

English · [中文](README.md)

[![Python](https://img.shields.io/badge/Python-3-3776AB?logo=python&logoColor=white)](#-requirements)
[![Claude Code Skills](https://img.shields.io/badge/Claude%20Code-Skills-D97757)](#-whats-inside)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows)](#-requirements)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

</div>

> 📝 **About the name**: this repo was previously unnamed. Based on what it does, the recommended name is **InfoDigest** (Chinese: 信息聚合).
> Alternatives: `SignalHub` / `DigestKit`. The text below uses InfoDigest — final choice is yours.

---

## Too much to read, too little time

Hundreds of new LinuxDo posts a day, a constantly churning Heybox feed, and Telegram channels that never stop pushing.
Reading it all isn't realistic — but skimming headlines means missing what actually matters.

**InfoDigest reads it for you, then hands you a digest of what's worth reading — and why.**

It's not keyword scraping. Every item passes through a two-dimensional thinking frame and gets a **0–10 "worth-reading" score**:

- **Information axis** — what does it say, and what does it deliberately leave out? Tutorial, resource, life/emotion, or discussion?
- **Influence axis** — what's its engagement hook? Is it trying to shape your perception and decisions, and how is it constructed?

## 🧩 What's inside

InfoDigest is a set of cooperating **Claude Code Skills** plus a data-layer CLI:

| Module | Source | What it does | Triggers |
| --- | --- | --- | --- |
| **forum-research** | LinuxDo (any forum) | Connects to your logged-in browser, reads new posts + comments, ranks by replies, writes a structured research report | "see what's on linux.do today", "community research" |
| **xiaoheihe-daily-digest** | Heybox · 盒友杂谈 | Harvests the top 60 hot posts, fetches bodies, synthesizes a deep daily observation report | "Heybox digest", "what are gamers discussing" |
| **channel-digest** | Telegram channels | Summarizes subscribed channels' last 24h into a newspaper-style HTML report, pushed to you via Bot | "channel digest", "run tg digest" |
| **`xiaoheihe-cli/xhh.py`** | Heybox | Data-layer CLI: scraping, ranking, credential management | `python xhh.py <command>` |

## ✨ Highlights

- 🧠 **Opinionated, not just aggregated** — a built-in "information × influence" frame plus a 0–10 score filters out the noise.
- 🌐 **Three sources, one tool** — forum, gaming community, and instant channels covered together.
- 🔐 **Uses your own session** — reads through your logged-in browser (forums block plain `fetch`); data never goes through a third party.
- 📰 **Newspaper-style reports** — Telegram digests are saved as styled HTML and pushed via Bot for later review.
- 🗣️ **Conversational triggers** — fire each skill with one plain-language sentence; no commands to memorize.

## 🚀 Quick start

> InfoDigest runs as Claude Code Skills, so set up the runtime first.

1. **Prerequisites** (see [Requirements](#-requirements)): Claude Code, Python 3, a logged-in browser, `tg-cli`.
2. **Central config**: copy `.claude/config.yaml.example` to `.claude/config.yaml` and fill in channels, output dirs, and Bot credential paths per module.
3. **Trigger in Claude Code with natural language**:

   ```text
   see what's on linux.do today      # → forum-research
   give me a Heybox digest           # → xiaoheihe-daily-digest
   run tg digest                     # → channel-digest
   ```

4. When Heybox credentials expire, run `python xiaoheihe-cli/xhh.py setup` and recapture from your browser's DevTools as prompted.

## 🔧 Requirements

- **Claude Code** (host for the skills)
- **Python 3** (the `xhh.py` data layer — zero third-party dependencies)
- **A logged-in browser** (forum/Heybox body fetching relies on your session, via the Claude-in-Chrome extension)
- **`tg-cli`** on PATH (Telegram reading and pushing)
- **A Telegram Bot** credential (pushes to you only; credentials live in a local `.env`, never written to chat or reports)

> 🔒 Security: all credentials are read locally and passed as variables — never printed to chat, written into HTML, or passed as command-line arguments.

## ⚙️ Configuration

Central config lives in `.claude/config.yaml`, organized into per-module sections (`channel_digest:`, `xiaoheihe:`, …).
Use `.claude/config.yaml.example` at the repo root as your template.

## 🗺️ Roadmap

- [ ] More sources (RSS / Weibo / X)
- [ ] Cross-platform (macOS / Linux)
- [ ] Merged view (all three sources in one report)

## 📄 License

[MIT](LICENSE)
