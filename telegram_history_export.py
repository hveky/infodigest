"""Export all accessible messages from one Telegram channel, with resumable pagination.

Uses the existing tg-cli session. Media files are not downloaded; metadata is retained.
"""

import argparse
import asyncio
import json
import re
import sqlite3
from contextlib import closing
from datetime import timezone
from pathlib import Path

from tg_cli.client import connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
"""

# Conservative text filters: avoid dropping legitimate posts merely mentioning ads or sex.
AD_PATTERNS = (
    re.compile(r"(?:182体育|1820755\.com|8G国际娱乐|PC28|视讯全网最高返水|世界杯官方指定投注|首存最高领|存U提)", re.I),
    re.compile(r"(?:商务合作|广告投放|招商加盟|频道互推|互推合作|广告位|接广告|承接投放)"),
    re.compile(r"(?:加|联系|咨询|私聊|私信)(?:我|客服|管理员)?[^\n]{0,24}(?:@[a-zA-Z0-9_]{5,}|微信|VX|vx|飞机|Telegram|TG)"),
    re.compile(r"(?:日入|躺赚|稳赚|被动收入|零成本暴富|零风险收益)[^\n]{0,50}(?:加群|私聊|报名|扫码|联系|进群|代理)"),
    re.compile(r"(?:出售|代充|兑换|收|出)\s*(?:USDT|U币|U)(?:\b|[：:])", re.I),
    # Specific repeated promotions; mentioning gambling in a news report is not enough.
    re.compile(r"6G全球顶级平台[｜|]官方直营"),
    re.compile(r"七大新春福利上线[\s\S]{0,200}存款送三次"),
    re.compile(r"铂莱娱乐[\s\S]{0,220}博彩信誉"),
    re.compile(r"球速体育[\s\S]{0,200}百家乐存50万"),
    re.compile(r"体育首周包赔体验[\s\S]{0,450}注册网址"),
    re.compile(r"U5贵宾会平台[\s\S]{0,120}U存U取"),
    re.compile(r"发财娱乐[\s\S]{0,120}线上国际娱乐城"),
    re.compile(r"历史累计充值达标领取回馈[\s\S]{0,140}返水加码"),
    re.compile(r"永旺哈希彩金[\s\S]{0,400}永旺德州彩金"),
    re.compile(r"【在售价】[\s\S]{0,50}【券后价】[\s\S]{0,200}(?:【下单链接】|淘✔寶|手机淘宝|更多福利大额券|复制去淘宝信息)"),
    re.compile(r"原价【[^】]{1,24}】[\s\S]{0,25}券后👉【[^】]{1,24}】"),
    re.compile(r"^📣[^\n]{0,40}(?:优惠总结|优惠合集|棉被合集)[\s\S]{0,220}🉐"),
    re.compile(r"(?:币安|Binance)注册[^\n]{0,30}返佣\s*20%[\s\S]{0,150}Gate注册[^\n]{0,30}返佣\s*20%", re.I),
    re.compile(r"点外卖前领红包下单【每天可领】[\s\S]{0,100}美团外卖红包[\s\S]{0,100}饿了么红包"),
    re.compile(r"每日领饿了么红包[\s\S]{0,45}【活动链接】"),
    re.compile(r"点外卖\s*提前领红包[\s\S]{0,40}每顿都有返利"),
    re.compile(r"【京东】【领券直降】[\s\S]{0,500}券后拼购价"),
    re.compile(r"(?:fuyezy\.com|fy\.maxiaoqiang\.com|此处内容已隐藏，请付费后查看|(?m:^#付费限时推广(?:\s|$))|#限时付费推广)", re.I),
)
PORN_PATTERNS = (
    re.compile(r"(?:成人视频|无码资源|成人资源|约炮|裸聊|看片入口|色情视频)"),
    re.compile(r"(?:福利姬|外围|楼凤|小姐上门|成人群)[^\n]{0,50}(?:加群|私聊|扫码|下载|点击|入口|联系)"),
    re.compile(r"全球成人网站排名Top\s*20\s*收好", re.I),
    re.compile(r"TGA年度最佳各个牌位奖给大家找到了[\s\S]{0,100}年度最佳黑丝"),
    re.compile(r"给躲台风的宝子们准备了下面车牌号"),
    re.compile(r"风俗店里工作的AV女优[\s\S]{0,70}会员频道里写过方法"),
)


def rejection_reason(text):
    if any(pattern.search(text) for pattern in PORN_PATTERNS):
        return "porn"
    if any(pattern.search(text) for pattern in AD_PATTERNS):
        return "ad"
    return None


def metadata(db, key):
    row = db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def store_metadata(db, key, value):
    db.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, str(value)))


def serialize(message):
    return {
        "id": message.id,
        "date": message.date.astimezone(timezone.utc).isoformat() if message.date else None,
        "text": message.message or "",
        "sender_id": message.sender_id,
        "media_type": type(message.media).__name__ if message.media else None,
        "reply_to_msg_id": message.reply_to.reply_to_msg_id if message.reply_to else None,
        "forwarded_from": message.fwd_from.to_dict() if message.fwd_from else None,
        "edit_date": message.edit_date.astimezone(timezone.utc).isoformat() if message.edit_date else None,
    }


def write_jsonl(db, destination):
    temporary = destination.with_name(destination.name + ".tmp")
    counts = {"kept": 0, "ad": 0, "porn": 0}
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            cursor = db.execute("SELECT payload FROM messages ORDER BY id")
            try:
                for (payload,) in cursor:
                    reason = rejection_reason(json.loads(payload)["text"])
                    if reason:
                        counts[reason] += 1
                        continue
                    output.write(payload + "\n")
                    counts["kept"] += 1
            finally:
                cursor.close()
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return counts


async def export(channel, destination, batch_size=200, max_messages=None):
    if batch_size < 1 or (max_messages is not None and max_messages < 1):
        raise ValueError("batch-size and max-messages must be positive")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = destination.with_name(destination.name + ".sqlite3")

    async with connect() as client:
        entity = await client.get_entity(channel)
        if not getattr(entity, "broadcast", False):
            raise ValueError("The selected chat is not a broadcast channel")
        with closing(sqlite3.connect(checkpoint)) as db:
            db.executescript(SCHEMA)
            saved_id = metadata(db, "channel_id")
            if saved_id is not None and saved_id != str(entity.id):
                raise ValueError("Checkpoint belongs to a different channel")
            with db:
                store_metadata(db, "channel_id", entity.id)
                store_metadata(db, "channel_name", getattr(entity, "title", ""))
                if not metadata(db, "first_scanned_id"):
                    row = db.execute("SELECT MAX(id) FROM messages").fetchone()
                    if row[0]:
                        store_metadata(db, "first_scanned_id", row[0])

            # Sanitize checkpoints produced by earlier versions before using them.
            removed = {"ad": 0, "porn": 0}
            cursor = db.execute("SELECT id, payload FROM messages")
            rejected = []
            try:
                for message_id, payload in cursor:
                    reason = rejection_reason(json.loads(payload)["text"])
                    if reason:
                        removed[reason] += 1
                        rejected.append((message_id,))
            finally:
                cursor.close()
            if rejected:
                with db:
                    db.executemany("DELETE FROM messages WHERE id = ?", rejected)
                db.execute("VACUUM")

            # Catch up from the first scanned ID, including rejected posts.
            first_scanned = int(metadata(db, "first_scanned_id") or 0)
            if not first_scanned:
                row = db.execute("SELECT MAX(id) FROM messages").fetchone()
                first_scanned = row[0] or 0
            if first_scanned:
                pending = []
                async for message in client.iter_messages(entity, min_id=first_scanned, reverse=True):
                    if not rejection_reason(message.message or ""):
                        data = serialize(message)
                        pending.append((message.id, json.dumps(data, ensure_ascii=False, default=str)))
                    if len(pending) >= batch_size:
                        with db:
                            db.executemany("INSERT OR IGNORE INTO messages (id, payload) VALUES (?, ?)", pending)
                        pending.clear()
                if pending:
                    with db:
                        db.executemany("INSERT OR IGNORE INTO messages (id, payload) VALUES (?, ?)", pending)

            if metadata(db, "complete") != "true":
                offset = int(metadata(db, "oldest_scanned_id") or 0)
                scanned = 0
                while True:
                    limit = min(batch_size, max_messages - scanned) if max_messages else batch_size
                    if limit == 0:
                        break
                    batch = await client.get_messages(entity, limit=limit, offset_id=offset)
                    if not batch:
                        with db:
                            store_metadata(db, "complete", "true")
                        break
                    with db:
                        for message in batch:
                            if message.id is None:
                                continue
                            data = serialize(message)
                            if rejection_reason(data["text"]):
                                continue
                            payload = json.dumps(data, ensure_ascii=False, default=str)
                            db.execute(
                                "INSERT OR IGNORE INTO messages (id, payload) VALUES (?, ?)",
                                (message.id, payload),
                            )
                        if not metadata(db, "first_scanned_id"):
                            store_metadata(db, "first_scanned_id", max(message.id for message in batch if message.id is not None))
                        offset = min(message.id for message in batch if message.id is not None)
                        store_metadata(db, "oldest_scanned_id", offset)
                    scanned += len(batch)
                    print(f"Scanned {scanned} messages this run; oldest ID {offset}", flush=True)
                    if max_messages is not None and scanned >= max_messages:
                        break
            counts = write_jsonl(db, destination)
            count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            complete = metadata(db, "complete") == "true"
            print(
                f"Stored/exported {count} to {destination}; "
                f"removed from checkpoint ads {removed['ad']}, porn {removed['porn']}; "
                f"excluded from output ads {counts['ad']}, porn {counts['porn']} "
                f"(complete={complete})"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", help="Channel @username, name, or numeric ID")
    parser.add_argument("output", type=Path, help="Destination JSONL file")
    parser.add_argument("--batch-size", type=int, default=200, help="Messages per request (default: 200)")
    parser.add_argument("--max-messages", type=int, help="Stop after this many scanned messages; rerun to continue")
    args = parser.parse_args()
    try:
        asyncio.run(export(args.channel, args.output, args.batch_size, args.max_messages))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"Export failed: {exc}\n")


if __name__ == "__main__":
    main()
