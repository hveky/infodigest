---
name: channel-digest
description: Use when the user asks for a Telegram channel digest, 频道日报, 跑 tg digest, channel digest, or telegram digest.
---

# Channel Digest

## Overview

把配置中的 Telegram 频道在指定 24 小时时间窗内的文字消息整理为一期"信息流观察"——单文件 Markdown 日报（.md），并按配置决定是否通过 Telegram Bot 推送。

读感要求：像一期正式的信息流观察文章，而不是逐频道复盘。全文不出现具体频道名，以命题为单位组织内容。

严格按 Step 1 到 Step 8 顺序执行。不要开启 subagent，不要创建定时任务。

## Paths and credentials

- 项目根目录：当前仓库根目录。
- 中央配置：`<repo>/.claude/config.yaml` 的 `channel_digest` 段。
- Bot 凭证：由 `channel_digest.auth.env_file` 指定；缺失时回退到 `E:\01-programs\tg-cli\.env`。
- 默认输出目录：`channel_digest.output_dir`。
- `tg` CLI 已在 PATH。

绝不在回复、报告、命令参数或日志中输出 `BOT_TOKEN`、`BOT_CHAT_ID`、Telegram API ID 或 API hash。读取凭证时只在进程内存中传递。

## Analysis frame

每条消息或同源消息组都从两个维度分析，并给出 0 到 10 的建议阅读分：

- 信息维度：区分事实与虚假叙事，识别说了什么、没说什么以及背后的权力关系；分类为实操教程、资源分享、情感与生活、科普讨论。
- 效力维度：识别流量钩子、内容结构以及它如何影响认知和决策。

## Workflow

任何联网动作之前加载并遵循 `web-access`；实际请求仍须遵守当前环境权限和审批规则。

### Step 1: Read config and refresh

1. 读取中央配置中的 `channel_digest`。
2. 配置不存在或缺少该段时立即停止，提示参考 `.claude/config.yaml.example`，不要自动创建配置。
3. 运行 `tg refresh --yaml`。失败时记录不含凭证的 warning 并继续。
4. 读取 `channels`：
   - 非空：对实际频道做 fuzzy match，未命中的频道记录 warning。
   - 空数组：运行 `tg chats --type channel --yaml` 获取全部 broadcast channel。

### Step 2: Export the time window

使用 `zoneinfo.ZoneInfo` 计算时间窗：

- `fixed_8am`：`until = 报告日期 08:00 Asia/Shanghai`，`since = until - 24h`；未传入 `report_date` 时报告日期取当前上海日期。
- `rolling_24h`：在开始采集时固定 `until = 当前时间`，`since = until - 24h`，不要因逐频道导出而移动窗口。
- 将边界转换为带时区的 UTC 时间。如果 `until` 尚在未来，不宣称窗口已完整结束，明确记录未覆盖时段。

`tg export --hours` 是相对 CLI 实际执行时的当前 UTC 时间回溯，且只读取本地消息缓存，不接受 `since/until`。不能用固定 `--hours 24` 代替日报窗口。每个频道导出前重新取 `export_now = datetime.now(timezone.utc)`，计算 `export_hours = max(1, math.ceil((export_now - since_utc).total_seconds() / 3600) + 1)`；额外一小时为执行延迟安全余量。两种窗口都按此公式扩大导出范围，再精确裁剪；若排队或执行延迟超过余量则重新计算并重导出。

```bash
tg export "<CHAT>" --hours "$EXPORT_HOURS" -f json -o <temp>/<safe_name>.json
```

读取 JSON 消息数组，时间字段为 `timestamp`（不是 `date`），正文为 `content`。解析 ISO 时间（兼容 `Z`），保留时区并统一到 UTC，然后严格按 `since_utc <= timestamp < until_utc` 过滤；缺失、无时区或不可解析的时间记录 warning，不能默认为本地时区。临时文件放在系统临时目录，不放进报告目录。

逐频道记录导出数、窗口内数、最早/最晚时间及窗口前后样本；与旧 `--hours 24` 的实际截止点比较，核对恢复的早段消息。CLI 每次最多返回 100000 条，达到上限时视为可能截断，不宣称覆盖完整。本地缓存缺历史时核查当前 CLI 的正常历史采集能力并在授权范围内补采；refresh/增量 sync 成功并不证明历史已补齐，样本首尾也不证明中间无缺口。仍缺历史或无法核实完整性时在报告和返回结果中明确覆盖限制，不用旧报告补缺。

### Step 3: Filter noise

按以下顺序过滤：

| 类型 | 丢弃条件 |
| --- | --- |
| 互推或纯转发 | 存在 `forwarded_from` 且文本不超过 20 字；或命中“推荐关注、频道互推、channel exchange、互推、大家好我是” |
| 广告 | 命中“招商、代理、USDT、contact @、投放、商务合作、承接、出U、收U、日入、被动收入、加我私聊” |
| 短水贴 | 纯文字不超过 `short_text_threshold`，且无 URL、代码块或白名单命中 |
| 媒体水贴 | 有媒体且 caption 不超过 `media_caption_threshold` |
| 系统消息 | service、pin、加群等系统通知 |

- `extra_spam_keywords` 追加到广告关键词。
- `extra_whitelist_keywords` 强制保留，覆盖其他过滤规则。
- 有效消息总数少于 5 时不写报告，直接说明当天信息不足。

### Step 4: Analyze

主对话读完全部有效消息。按 Analysis frame 为每条内容形成分类、事实缺口、钩子机制和建议阅读分。不要写 Markdown 中间文件。

### Step 5: Rank

按建议阅读分降序：

- 前 3 条：建议阅读。
- 中段：可看。
- 尾段：可不看。

### Step 6: Write the article

正文使用中文文章体，结构固定为四节：**总论 → 信息内容 → 传播机制 → 总结**。

1. 大标题：有洞见的反问句。
2. 副标题：一句话指出当天内容的核心张力。
3. 报头块（引用块）：日期、期号、时间窗、有效消息数。
4. 总论（500 到 1000 字）：给出当天信息流的整体结构判断与核心张力，统领全文。
5. 信息内容（2500 到 4000 字）：以命题为单位串联消息，分析说了什么、没说什么、内容之间的共振或对照。不按频道分节，不出现频道名；需要归属时只用泛称（如"财经类频道"）。
6. 传播机制（1500 到 2500 字）：分析分发流水线、钩子与悬念、清单体、人设包装、可信度工程、认知改造方式等效力装置。
7. 总结（400 到 800 字）：收束当日判断；附推荐阅读 3 条，每条含 30 字内理由和原文链接（不含频道名）。
8. 同一内容被多频道重复分发的，正文只出现一次；重复本身作为现象在"传播机制"中描述。
9. 生成后计算正文中文字符数；少于 6000 时继续完善。

原文链接：

- public channel：`https://t.me/<username>/<message_id>`
- private channel：`https://t.me/c/<id_without_-100>/<message_id>`

### Step 7: Write Markdown

单文件 Markdown，保存为 `<output_dir>/YYYY-MM-DD.md`。目录不存在时创建。

- 报头块、大标题、副标题之后，四个一级小节依次为 `总论`、`信息内容`、`传播机制`、`总结`。
- 消息原文以引文或行内引用呈现，注意转义。
- 不生成 HTML，不使用样式代码。
- 文末以一行小字注明生成时间与 `channel-digest v1`。

### Step 8: Push

合刊模式必须跳过本节，禁止推送单份。

当 `push.enabled` 为 `false` 时只保存报告。

当推送启用时：

1. 从 `auth.env_file` 读取 `BOT_TOKEN` 和 `BOT_CHAT_ID`。
2. 在 Python 进程内用 `requests.post` 调用 Telegram Bot `sendDocument`；token 只存在于内存构造的 URL 中，不打印 URL。
3. caption 使用大标题；`caption_with_title` 为 `false` 时使用 `频道日报 YYYY-MM-DD`。
4. caption 截断到 1024 字符。
5. 推送失败时保留 MD，只报告状态码和脱敏错误。

## Safety

- 不抓取或保存图片、视频、音频。
- 不修改 `E:\01-programs\tg-cli`。
- 不向报告写入凭证或私有元数据。
- 不覆盖同日既有报告，除非用户明确要求；默认使用 `_2`、`_3` 后缀。
- 所有联网与写入动作服从当前环境权限和审批规则。

## Verification

完成后核对：

1. `tg whoami --yaml` 成功。
2. 输出 MD 存在且正文中文字符数不少于 6000。
3. 全文不含具体频道名；四节结构完整；链接格式正确。
4. 推送启用时 Telegram 收到 MD 文件与正确 caption。

## 合刊模式接口（优先于单份推送）

明确点名本来源时保持上述单份流程；由 `daily-digest` 父入口传入 `mode=combined` 和 `report_date=YYYY-MM-DD` 时：
- 使用父入口核实的上海报告日期写报头和文件名，仍保留本来源真实采集窗口、样本数量和限制，不伪称统一窗口。
- 完整正文照常保存为 Markdown，不覆盖同日文件，默认 `_2`、`_3`。
- **禁止单份推送**，跳过所有单份 Bot 步骤，即使 `channel_digest.push.enabled=true`；仅父入口可以调用独立合刊推送脚本。
- 返回本轮实际绝对 Markdown 路径、报告日期、采集窗口/样本限制、正文中文字符数、质量检查通过/失败及原因；失败不得声称完整合刊。
- 不搜索最新报告、不用历史文件补缺。恢复只复用父入口明确确认的本轮路径。
- Telegram 子技能不自行开启 subagent 的规则不变；父入口可以调用它。
