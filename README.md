# 信息聚合

“跑一下今天的”“三合一”“信息聚合日报”默认调用 `daily-digest`：Linux.do、小黑盒和 Telegram 三份全文 Markdown 合刊为 PDF（封面、实际页码目录、三章、书签、原文链接），仅推送合刊 PDF。明确点名某来源仍运行其单份技能。两套 `.claude/skills` / `.agents/skills` 内容保持一致；来源采集与内容规则以同步后的技能为准。

## 安装与离线构建

Python 3.10+，`python -m pip install -r requirements.txt`。中文字体默认本机 `C:/Windows/Fonts/simhei.ttf`，可用 `--font` 显式指定 TTF；缺失时报错，不下载。配置 `daily_digest.output_dir: reports/combined`，相对仓库根解析；此目录已由 `reports/` 忽略。

```bash
python -B scripts/build_digest_pdf.py --date 2030-01-02 --linuxdo /absolute/forum.md --xiaoheihe /absolute/heybox.md --telegram /absolute/telegram.md --output-dir reports/combined
```

只接受三个明确 Markdown 路径和一致报头日期，论坛正文 ≥6000、小黑盒 ≥10000、TG ≥6000 中文字符，TG 四节依次为总论/信息内容/传播机制/总结。不会寻找“最新”文件或读取网络资源。标题、引文、列表、代码和表格文字全部保留；表格降级为逐行文本（列以 `|` 分隔），便于长单元格跨页。远程图片不加载，保留替代文字与地址；HTML 不执行，script/style 安全略过。长 URL 可分页，HTTP(S) 原文链接可点击。

原子构建，不覆盖成品，同日自动 `_2`、`_3`。每份 PDF 配套 `.manifest.json`，记录三个显式源路径、日期、中文字符数和 SHA-256、PDF 哈希，无凭证。缺源/质量失败不发布完整合刊。恢复只补失败来源，复用原路径；成品恢复可在原构建命令添加 `--resume /absolute/file.manifest.json`，内容或日期变更会拒绝。

## 独立安全推送

构建完全离线；下面命令才可能访问网络和现有 `channel_digest.auth.env_file`（只在进程内读取）：

```bash
python -B scripts/push_digest_pdf.py --pdf /absolute/file.pdf --manifest /absolute/file.manifest.json --config /absolute/.claude/config.yaml
```

复用 `channel_digest.push.enabled` 和 `caption_with_title`，不增加推送开关。合刊子任务禁止发送单份。成功状态存 `.push.json` 并防重；`--resend` 仅供用户明确要求重发。超时、断连、中断、服务端异常或无效响应标为 **unknown**，不自动重试：先确认是否收到，再在用户明确要求时使用 `--resend --confirm-unknown`。残留 `.push.lock` 说明进程可能异常退出，须人工核对并处理，不能自动删锁重发。Bot API 无幂等键，不保证绝对恰好一次。失败保留 PDF，不输出凭证、请求 URL 或响应详情。

## 验证

```bash
python -m unittest discover -s tests -v
```

测试只使用合成 Markdown、临时目录、虚构凭证和 mock HTTP，不读取真实凭证/今日报告、不联网不推送。PDF 检查使用 pypdf 和 PyMuPDF；离线合成验收样本可另生成至忽略目录 `reports/combined/_verification`。
