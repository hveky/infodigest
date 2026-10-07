"""Explicit Telegram PDF delivery; imports never read credentials or send requests."""

from __future__ import annotations

import argparse
import hashlib
import json
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile

import requests
import yaml


class PushError(ValueError):
    """Only sanitized, fixed messages are exposed to callers."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_state(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".state.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def credentials(path: Path) -> tuple[str, str]:
    values = {}
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(
                r"(?:export\s+)?(BOT_TOKEN|BOT_CHAT_ID)\s*=\s*(.*?)\s*$", line.strip()
            )
            if match:
                values[match[1]] = match[2].strip("\"'")
    except (OSError, UnicodeError):
        raise PushError("Bot credential file unavailable") from None
    token, chat = values.get("BOT_TOKEN", ""), values.get("BOT_CHAT_ID", "")
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) or not re.fullmatch(
        r"-?\d+|@[A-Za-z0-9_]+", chat
    ):
        raise PushError("Bot credentials missing or invalid")
    return token, chat


def push(
    pdf: Path,
    manifest_path: Path,
    config_path: Path,
    *,
    resend: bool = False,
    confirm_unknown: bool = False,
    post=None,
) -> str:
    """One request maximum. Unknown delivery requires human confirmation, never retry."""
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError()
        channel = config["channel_digest"]
        if not isinstance(channel, dict):
            raise ValueError()
        settings = channel["push"]
        if not isinstance(settings, dict) or not isinstance(
            settings.get("enabled"), bool
        ):
            raise ValueError()
        if not settings["enabled"]:
            return "disabled"
        if not isinstance(settings.get("caption_with_title", True), bool):
            raise ValueError()
        auth = channel["auth"]
        if (
            not isinstance(auth, dict)
            or not isinstance(auth.get("env_file"), str)
            or not auth["env_file"]
        ):
            raise ValueError()
        env_path = Path(auth["env_file"])
        if pdf.suffix.lower() != ".pdf":
            raise ValueError()
        content = pdf.read_bytes()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fingerprint = digest(content)
        if (
            Path(manifest["pdf"]).resolve() != pdf.resolve()
            or manifest["pdf_sha256"] != fingerprint
            or not content.startswith(b"%PDF-")
            or len(manifest["sources"]) != 3
        ):
            raise ValueError()
        # Detect changed explicit sources before sending a previously built artifact.
        for source in manifest["sources"]:
            if (
                source["date"] != manifest["date"]
                or digest(Path(source["path"]).read_bytes()) != source["sha256"]
            ):
                raise ValueError()
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, yaml.YAMLError):
        raise PushError(
            "Invalid configuration, PDF, or manifest/source hashes"
        ) from None
    state_path = pdf.with_suffix(".push.json")
    lock_path = pdf.with_suffix(".push.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise PushError(
            "Delivery lock exists; verify receipt and resolve interrupted delivery manually"
        ) from None
    except OSError:
        raise PushError("Delivery lock unavailable; details withheld") from None
    os.close(fd)
    try:
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state["pdf_sha256"] != fingerprint or state["status"] not in {
                    "sent",
                    "unknown",
                    "failed",
                }:
                    raise ValueError()
            except (OSError, ValueError, KeyError, TypeError):
                raise PushError(
                    "Invalid delivery state; manual inspection required"
                ) from None
            if state["status"] == "sent" and not resend:
                return "already_sent"
            if state["status"] == "unknown" and not (resend and confirm_unknown):
                raise PushError(
                    "Delivery unknown; confirm receipt before explicit --resend --confirm-unknown"
                )
        token, chat = credentials(env_path)
        # Persist uncertainty before network I/O, including interruption/process termination.
        state = dict(version=1, pdf_sha256=fingerprint, status="unknown")
        write_state(state_path, state)
        title = (
            "信息聚合日报 · 三份全文合刊"
            if settings.get("caption_with_title", True)
            else "信息聚合日报"
        )
        try:
            with BytesIO(content) as stream:
                response = (post or requests.post)(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={
                        "chat_id": chat,
                        "caption": f"{title} {manifest['date']}"[:1024],
                    },
                    files={"document": (pdf.name, stream, "application/pdf")},
                    timeout=(10, 60),
                    allow_redirects=False,
                )
            if response.status_code != 200:
                # Server/proxy failure may follow successful delivery: remain unknown.
                if 400 <= response.status_code < 500:
                    state["status"] = "failed"
                    write_state(state_path, state)
                raise PushError(
                    f"Telegram HTTP {response.status_code}; response details withheld"
                )
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                if isinstance(payload, dict) and payload.get("ok") is False:
                    state["status"] = "failed"
                    write_state(state_path, state)
                raise PushError(
                    "Telegram rejected or returned invalid response; details withheld"
                )
            state["status"] = "sent"
            write_state(state_path, state)
            return "sent"
        except PushError:
            raise
        except Exception:
            raise PushError(
                "Delivery unknown; verify receipt before retry (error details withheld)"
            ) from None
    except OSError:
        raise PushError(
            "Delivery state unavailable; details withheld; verify receipt before retry"
        ) from None
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            # Keep the safety lock and surface only a fixed message, never raw paths/errors.
            raise PushError(
                "Delivery lock cleanup failed; verify receipt and inspect state manually"
            ) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resend", action="store_true")
    parser.add_argument("--confirm-unknown", action="store_true")
    args = parser.parse_args()
    try:
        print(
            push(
                args.pdf,
                args.manifest,
                args.config,
                resend=args.resend,
                confirm_unknown=args.confirm_unknown,
            )
        )
    except PushError as exc:
        parser.exit(1, f"Push failed: {exc}\n")


if __name__ == "__main__":
    main()
