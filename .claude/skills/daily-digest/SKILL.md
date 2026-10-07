---
name: daily-digest
description: 默认三合一信息聚合日报。用户说“三合一”“信息聚合日报”“跑一下今天的”时生成三份全文合刊 PDF，仅推送 PDF；明确点名 Linux.do/论坛、小黑盒或 Telegram/频道时优先对应单份技能。
---

# 信息聚合日报 · 三份全文合刊

## 路由和边界

- “跑一下今天的”默认三合一；明确点名单一来源优先 `forum-research`、`xiaoheihe-daily-digest` 或 `channel-digest` 的原单份流程。
- 合刊只增加封面、实际页码目录和三个章节，保留三份全文，不跨来源重写、摘要替代正文或删减。
- 不自动定时、不扫描“最新文件”、不拿历史日期补缺。
- “仅合刊已有文件”必须由用户明确指定日期和三个 Markdown 文件路径；不自动转换今日旧 HTML。
- 所有权限、安全和联网动作遵守当前环境规则；子技能联网前加载 `web-access`。安全模型或权限不可用时立即停止说明，不绕过。

## 流程

1. 用 `datetime.now(ZoneInfo("Asia/Shanghai"))` 核实上海日期；读取 `<repo>/.claude/config.yaml`，取 `daily_digest.output_dir`（缺段/字段时回退 `<repo>/reports/combined`，配置值相对仓库根解析）。不读取凭证本体。
2. 调用三个子技能，均明确传入 `mode=combined`、同一 `report_date`。Telegram 子技能不得自身开启子任务。各子技能仍按自己的实际窗口/样本与写作标准执行，不伪装统一窗口。**合刊模式禁止任何单份推送，无论配置开关。**
3. 收集本轮三份明确绝对 Markdown 路径、日期、窗口/样本限制、质量检查结果和正文中文字符数（论坛 ≥6000、小黑盒 ≥10000、Telegram ≥6000）。TG 四节为总论、信息内容、传播机制、总结；不点名频道、重复内容合并。
4. 缺源、子任务失败或质量检查未过：不发布完整三合一，不推送；保留已完成单份并说明缺口。恢复时复用已确认本轮路径，只补失败来源，不全部重新采集。
5. 仅显式路径离线构建（Git Bash，替换占位符为绝对路径）：
   ```bash
   python -B "<repo>/scripts/build_digest_pdf.py" --date YYYY-MM-DD --linuxdo "<本轮论坛.md>" --xiaoheihe "<本轮小黑盒.md>" --telegram "<本轮TG.md>" --output-dir "<合刊目录>" --font "C:/Windows/Fonts/simhei.ttf"
   ```
   排版为单栏中文编辑刊物：精简封面、可点击真实页码目录、黑体分级标题、宋体长文正文（同目录本地 `simsun.ttc` 可用时），墨色与单一强调色、来源页眉与页脚、区别引用/列表/表格/代码。命名链接不额外展开完整 URL，原标签与完整目标注释保留；原文裸网址、代码空格、编号和所有正文不得删减。脚本验证本地字体真实中文字形覆盖，不下载字体或启动浏览器服务。
   脚本拒绝缺源、空源、非 Markdown、日期不符、字数/四节不合格与不存在字体；不下载字体。输出不覆盖，同日使用 `_2`、`_3`。记录 `.manifest.json`，含三个显式源路径、日期、中文字符数、SHA-256 和 PDF 哈希，无凭证。
6. 使用 pypdf/PyMuPDF 验证中文可提取、三章首尾正文、封面目录真实页码、嵌入中文字体、分页、书签和原文链接；渲染代表页检查，不启动浏览器服务器。失败不推送。
7. 仅明确调用独立推送脚本，复用 `channel_digest.push.enabled` 和 `caption_with_title`，只发送一个合刊 PDF：
   ```bash
   python -B "<repo>/scripts/push_digest_pdf.py" --pdf "<实际合刊.pdf>" --manifest "<实际合刊.manifest.json>" --config "<repo>/.claude/config.yaml"
   ```
   凭证只由该进程从现有 `channel_digest.auth.env_file` 读取，不 import 全局 `send_tg.py`。推送状态及 PDF 哈希存合刊目录。成功不自动重发；超时、中断或状态 unknown 不自动 retry，先请用户确认是否收到。用户确认后且明确要求重发才可 `--resend --confirm-unknown`。不承诺绝对恰好一次。
8. 回复简短三来源结论、窗口/样本限制、实际 PDF 链接及推送状态，不重复粘贴全文。

## 恢复

- 复用已完成成品时，显式传入原清单 `--resume "<manifest>"` 和原日期/三个路径；脚本拒绝源内容哈希变化、PDF 变化或混日期。
- 构建与推送分离；发送失败保留 PDF，错误脱敏，禁止输出 token/chat ID/请求 URL/响应详情。
- 清单、状态、PDF 必须落在已忽略的 `reports/combined` 或另一个确认已被忽略的私有目录。
