"""Forward Discord channel image/video uploads to similarly named Telegram topics.

Scheduled GitHub Actions job; no always-running host required.
Uses Discord Bot API and Telegram Bot API (not a user account/self-bot).
"""

from __future__ import annotations

import difflib
import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken

DISCORD_API = "https://discord.com/api/v10"
TELEGRAM_API = "https://api.telegram.org"
STATE_FILE = Path(__file__).with_name("router_state.enc")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".avif")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".mpeg", ".mpg", ".3gp")
NOISE_WORDS = {"discord", "telegram", "tg", "channel", "topic", "photos", "photo", "pictures", "picture", "images", "image", "videos", "video", "media", "uploads", "updates", "clips", "posts"}
MAX_BYTES = 50 * 1024 * 1024  # Telegram public Bot API upload cap for sendVideo/sendDocument.
SESSION = requests.Session()


def get_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing GitHub Actions secret {name}")
    return value


class TelegramHttpError(RuntimeError):
    def __init__(self, status_code: int, description: str):
        self.status_code = status_code
        super().__init__(f"Telegram HTTP {status_code}: {description}")


class MediaTooLarge(ValueError):
    """A media attachment cannot be uploaded through the hosted Bot API."""


def api_request(method: str, url: str, *, params=None, data=None, files=None, json_body=None, timeout=40):
    """Basic 429/5xx retry; return JSON response after HTTP validation."""
    for attempt in range(5):
        if files:
            for item in files.values():
                file_obj = item[1] if isinstance(item, tuple) else item
                if hasattr(file_obj, "seek"):
                    file_obj.seek(0)
        try:
            response = SESSION.request(method, url, params=params, data=data, files=files, json=json_body, timeout=timeout)
        except requests.RequestException:
            if attempt == 4:
                raise RuntimeError("Telegram API network request failed") from None
            time.sleep(min(2 ** attempt, 16))
            continue
        if response.status_code == 429:
            try:
                body = response.json()
                wait = float(body.get("parameters", {}).get("retry_after") or body.get("retry_after") or 0)
            except (ValueError, TypeError):
                wait = 0
            wait = wait or float(response.headers.get("Retry-After", 2))
            time.sleep(min(max(wait, 1), 60))
            continue
        if response.status_code in {500, 502, 503, 504} and attempt < 4:
            time.sleep(min(2 ** attempt, 16))
            continue
        try:
            result = response.json()
        except ValueError:
            result = {}
        if not response.ok:
            raise TelegramHttpError(response.status_code, result.get("description", "API rejected request"))
        return result
    raise RuntimeError("Telegram API repeatedly rate limited this operation")


def telegram(token, method, *, data=None, files=None):
    result = api_request("POST", f"{TELEGRAM_API}/bot{token}/{method}", data=data, files=files, timeout=130)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method}: {result.get('description', 'Unknown error')}")
    return result.get("result")


def discord_request(token, route, params=None):
    for attempt in range(5):
        try:
            response = SESSION.get(
                f"{DISCORD_API}{route}",
                headers={"Authorization": f"Bot {token}"},
                params=params,
                timeout=40,
            )
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 429:
            delay = response.json().get("retry_after", 2)
            time.sleep(min(max(float(delay), 1), 60))
            continue
        if response.status_code >= 500 and attempt < 4:
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("Discord repeatedly rate limited the bot")


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def canonical_name(value: str) -> str:
    words = normalize_name(value).split()
    simplified = [word for word in words if word not in NOISE_WORDS]
    return " ".join(simplified) if simplified else " ".join(words)


def match_topic(channel_name: str, topics: dict) -> tuple[str | None, str]:
    """Find a unique confident name match; NEVER guess ambiguous destinations.

    topics: mapping thread ID strings to {name: str}.
    """
    if not topics:
        return None, "no topics registered"
    if not normalize_name(channel_name):
        return None, "channel name has no recognizable letters or digits"
    entries = [(str(topic_id), item["name"]) for topic_id, item in topics.items()]
    exact = [(topic_id, name) for topic_id, name in entries if normalize_name(name) == normalize_name(channel_name)]
    if len(exact) == 1:
        return exact[0][0], "exact name"
    if len(exact) > 1:
        return None, "ambiguous exact names"
    source = canonical_name(channel_name)
    canonical = [(topic_id, name) for topic_id, name in entries if canonical_name(name) == source]
    if len(canonical) == 1:
        return canonical[0][0], "similar name (platform/media suffix ignored)"
    if len(canonical) > 1:
        return None, "ambiguous similar names"
    if len(source) < 5:
        return None, "no safe name match (short name)"
    scores = sorted(
        [(difflib.SequenceMatcher(None, source, canonical_name(name)).ratio(), topic_id, name) for topic_id, name in entries],
        reverse=True,
    )
    best = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0
    if best[0] >= 0.89 and best[0] - runner_up >= 0.12:
        return best[1], f"close spelling ({best[0]:.0%})"
    if best[0] >= 0.70:
        return None, f"possible topic ({best[0]:.0%}); not confident enough"
    return None, "no similar topic found"


def load_routing_config() -> dict:
    """Private topic inventory and explicit channel-ID overrides from a secret."""
    raw = os.environ.get("ROUTER_CONFIG_JSON", "").strip()
    if not raw:
        return {"topics": {}, "channel_overrides": {}}
    try:
        config = json.loads(raw)
    except (TypeError, ValueError):
        raise RuntimeError("ROUTER_CONFIG_JSON must be a JSON object") from None
    if not isinstance(config, dict):
        raise RuntimeError("ROUTER_CONFIG_JSON must be a JSON object")
    topics = config.get("topics", {})
    overrides = config.get("channel_overrides", {})
    if not isinstance(topics, dict) or not isinstance(overrides, dict):
        raise RuntimeError("ROUTER_CONFIG_JSON topics and channel_overrides must be objects")
    for tid, item in topics.items():
        if not str(tid).isdigit() or int(tid) < 1 or not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise RuntimeError("Each configured topic needs a positive topic ID and a name")
    for cid, tid in overrides.items():
        if not str(cid).isdigit() or not isinstance(tid, str) or not tid.isdigit() or int(tid) < 1:
            raise RuntimeError("Channel overrides must map channel IDs to positive topic-ID strings")
    return {**config, "topics": topics, "channel_overrides": overrides}


def resolve_channel_route(channel: dict, topics: dict, overrides: dict) -> tuple[str | None, str]:
    """Explicit IDs take priority, even when the channel is later renamed."""
    target = overrides.get(str(channel["id"]))
    if target is not None:
        if target not in topics:
            return None, "explicit destination is missing; automatic matching disabled for this channel"
        return target, "explicit channel-to-topic mapping"
    return match_topic(channel["name"], topics)


def channel_label(channel: dict) -> str:
    # This is a public repository: private channel/topic names stay out of logs.
    return "channel " + hashlib.sha256(str(channel["id"]).encode()).hexdigest()[:10]


def default_state() -> dict:
    return {"version": 1, "telegram_offset": 0, "topics": {}, "channels": {}}


def load_state() -> dict:
    if not STATE_FILE.exists():
        return default_state()
    try:
        state = json.loads(state_cipher().decrypt(STATE_FILE.read_bytes()))
    except InvalidToken:
        raise RuntimeError("Cannot decrypt forwarding state. Restore the original Telegram bot token before resuming; do not reset progress blindly") from None
    if state.get("version") != 1:
        raise RuntimeError("Unexpected forwarding-state format")
    return state


def state_cipher() -> Fernet:
    # Bot tokens are high-entropy credentials. Domain separation ensures that
    # this key is used only for router state; no extra user-managed key is needed.
    digest = hashlib.sha256(b"discord-telegram-state-v1:" + get_secret("TELEGRAM_BOT_TOKEN").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def save_state(state: dict):
    cipher = state_cipher()
    payload = json.dumps(state, sort_keys=True, ensure_ascii=False).encode()
    if STATE_FILE.exists():
        try:
            if cipher.decrypt(STATE_FILE.read_bytes()) == payload:
                return
        except InvalidToken:
            raise RuntimeError("Refusing to overwrite forwarding state encrypted with another bot token") from None
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_bytes(cipher.encrypt(payload))
    tmp.replace(STATE_FILE)


def poll_registration_commands(token: str, group_id: str, owner_user_id: str, state: dict):
    """Read commands in Telegram forum topics to learn ID -> display name mapping."""
    changed = 0
    for _ in range(20):
        updates = telegram(token, "getUpdates", data={
            "offset": state["telegram_offset"], "timeout": 0, "limit": 100,
            "allowed_updates": json.dumps(["message"]),
        })
        if not updates:
            break
        for update in updates:
            state["telegram_offset"] = update["update_id"] + 1
            msg = update.get("message") or {}
            chat = msg.get("chat") or {}
            if str(chat.get("id", "")) != group_id:
                continue
            tid = msg.get("message_thread_id")
            if msg.get("forum_topic_created") and str((msg.get("from") or {}).get("id", "")) == owner_user_id:
                topic = msg["forum_topic_created"]
                topic_name = topic.get("name")
                # Telegram's first topic service message is sometimes its thread ID.
                tid = tid or msg.get("message_id")
                if tid and topic_name:
                    state["topics"][str(tid)] = {"name": topic_name}
                    changed += 1
                continue
            if msg.get("forum_topic_edited") and tid and str((msg.get("from") or {}).get("id", "")) == owner_user_id:
                new_name = msg["forum_topic_edited"].get("name")
                if new_name:
                    state["topics"][str(tid)] = {"name": new_name}
                    changed += 1
                continue
            command = (msg.get("text") or "").strip()
            parsed = re.match(r"^/(bind|unbind|topics)(?:@[a-zA-Z0-9_]+)?(?:\s+(.+))?$", command, re.I | re.S)
            if not parsed:
                continue
            sender = msg.get("from") or {}
            if str(sender.get("id", "")) != owner_user_id:
                print("Ignored registration command from an unauthorized user")
                continue
            action, name = parsed.group(1).lower(), (parsed.group(2) or "").strip()
            if action == "topics":
                count = len(state["topics"])
                telegram(token, "sendMessage", data={"chat_id": group_id, **({"message_thread_id": tid} if tid else {}), "text": f"Registered topics: {count}. Use /bind Topic Name in each existing topic."})
            elif action == "bind":
                if not tid or not name:
                    print("/bind must be sent inside a Telegram topic with its name: /bind WTJ")
                    continue
                state["topics"][str(tid)] = {"name": name[:128]}
                changed += 1
                telegram(token, "sendMessage", data={"chat_id": group_id, "message_thread_id": tid, "text": f"Topic saved as '{name[:128]}'. I'll match Discord channel names automatically."})
            elif action == "unbind" and tid:
                if state["topics"].pop(str(tid), None):
                    changed += 1
                telegram(token, "sendMessage", data={"chat_id": group_id, "message_thread_id": tid, "text": "This topic has been removed from forwarding matches."})
            save_state(state)
        save_state(state)
        if len(updates) < 100:
            break
    else:
        raise RuntimeError("Telegram update queue too long; rerun to catch up")
    print(f"Known Telegram topics: {len(state['topics'])} (registration changes: {changed})")


def list_channels(token: str, guild_id: str) -> list[dict]:
    data = discord_request(token, f"/guilds/{guild_id}/channels")
    return sorted((ch for ch in data if ch.get("type") in (0, 5)), key=lambda ch: ch.get("name", "").lower())


def media_attachments(message: dict) -> list[dict]:
    """Uploads plus direct Discord-hosted media URLs; ordinary links are ignored."""
    found = []
    seen = set()

    def add(item):
        url = item.get("url", "")
        parts = urlsplit(url)
        key = parts.path if parts.hostname in {"cdn.discordapp.com", "media.discordapp.net"} else url
        if key and key in seen:
            return
        if key:
            seen.add(key)
        found.append(item)

    for item in message.get("attachments", []):
        name = (item.get("filename") or "").lower()
        mime = (item.get("content_type") or "").lower()
        if mime.startswith("image/") or name.endswith(IMAGE_EXTENSIONS):
            add({**item, "_kind": "photo"})
        elif mime.startswith("video/") or name.endswith(VIDEO_EXTENSIONS):
            add({**item, "_kind": "video"})
    urls = re.findall(r"https://(?:cdn\.discordapp\.com|media\.discordapp\.net)/(?:ephemeral-attachments|attachments)/[^\s<>\"']+", message.get("content") or "", re.I)
    for embed in message.get("embeds", []):
        urls.append(embed.get("url") or "")
        for field in ("image", "video"):
            urls.append((embed.get(field) or {}).get("url") or "")
    for url in urls:
        item = discord_media_url(url.rstrip(")]},.;"))
        if item is not None:
            add(item)
    return found


def discord_media_url(url: str) -> dict | None:
    """Only genuine attachment URLs on Discord's two media hosts qualify."""
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in {"cdn.discordapp.com", "media.discordapp.net"} or parts.username or parts.password or parts.port not in (None, 443):
            return None
    except ValueError:
        return None
    if not re.fullmatch(r"/(?:ephemeral-attachments|attachments)/[0-9]+/[0-9]+/[^/]+", parts.path):
        return None
    filename = Path(unquote(parts.path.rsplit("/", 1)[-1])).name
    lowered = filename.casefold()
    if lowered.endswith(IMAGE_EXTENSIONS):
        kind = "photo"
    elif lowered.endswith(VIDEO_EXTENSIONS):
        kind = "video"
    else:
        return None
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return {"url": parts._replace(fragment="").geturl(), "filename": filename, "content_type": mime, "_kind": kind}


def get_new_messages(token: str, channel_id: str, last_message_id: str) -> list[dict]:
    messages = []
    before = None
    for page_number in range(100):
        params = {"limit": 100}
        if before:
            params["before"] = before
        page = discord_request(token, f"/channels/{channel_id}/messages", params)
        if not page:
            break
        stopped = False
        for msg in page:
            if int(msg["id"]) < int(last_message_id):
                stopped = True
                break
            messages.append(msg)
        if stopped or len(page) < 100:
            break
        before = page[-1]["id"]
    else:
        raise RuntimeError(f"Channel {channel_id} exceeded 10,000 messages between runs; cursor unchanged")
    return list(reversed(messages))


def download_media(item: dict) -> bytes:
    expected_size = int(item.get("size") or 0)
    if expected_size > MAX_BYTES:
        raise MediaTooLarge("Media exceeds the hosted Telegram Bot API upload limit")
    try:
        with SESSION.get(item["url"], timeout=100, stream=True, allow_redirects=False) as response:
            if 300 <= response.status_code < 400:
                raise RuntimeError("Discord media redirected unexpectedly; no file or link was forwarded")
            response.raise_for_status()
            data = io.BytesIO()
            for chunk in response.iter_content(chunk_size=256 * 1024):
                data.write(chunk)
                if data.tell() > MAX_BYTES:
                    raise MediaTooLarge("Media exceeds the hosted Telegram Bot API upload limit")
            return data.getvalue()
    except requests.RequestException:
        # Signed CDN URLs must not leak through public workflow error logs.
        raise RuntimeError("Discord media download failed; a copied media URL may have expired") from None


def send_media(token: str, group_id: str, topic_id: str, item: dict):
    # Forward actual media files only. Never substitute a source URL or text.
    if int(item.get("size") or 0) > MAX_BYTES:
        raise MediaTooLarge("Media exceeds the hosted Telegram Bot API upload limit")
    media = download_media(item)
    filename = item.get("filename") or "discord-upload"
    mime = (item.get("content_type") or "application/octet-stream").split(";")[0]
    kind = item["_kind"]
    name_lower = filename.lower()
    # sendVideo is for MP4; other videos preserve their original format as a Telegram file.
    if kind == "photo" and len(media) <= 10 * 1024 * 1024:
        operations = [("sendPhoto", "photo"), ("sendDocument", "document")]
    elif kind == "video" and (name_lower.endswith((".mp4", ".m4v")) or mime == "video/mp4"):
        operations = [("sendVideo", "video"), ("sendDocument", "document")]
    else:
        operations = [("sendDocument", "document")]
    for pos, (method, field) in enumerate(operations):
        try:
            telegram(token, method, data={"chat_id": group_id, "message_thread_id": topic_id}, files={field: (filename, io.BytesIO(media), mime)})
            print(f"  Sent {kind} ({len(media)} bytes) via {method}")
            return
        except TelegramHttpError as exc:
            # Telegram sometimes rejects a photo/video format; fall back to document.
            if pos < len(operations) - 1 and exc.status_code == 400:
                print("  Media preview rejected; retrying as document")
                continue
            raise
    raise RuntimeError("Telegram did not accept the upload")


def process_discord(token: str, guild_id: str, telegram_token: str, group_id: str, state: dict, *, overrides=None):
    channels = list_channels(token, guild_id)
    print(f"Found {len(channels)} Discord text/announcement channels")
    if not channels:
        raise RuntimeError("No Discord text channels found; check the server ID and bot membership")
    routes = {}
    for ch in channels:
        target, explanation = resolve_channel_route(ch, state["topics"], overrides or {})
        routes[ch["id"]] = target
        label = "matched Telegram topic" if target else "SKIPPED (no safe topic match)"
        print(f"  {channel_label(ch)} -> {label} [{explanation}]")
    if not any(routes.values()):
        raise RuntimeError("No Discord channels match a registered Telegram topic. Send /bind Exact Topic Name inside each matching Telegram topic")
    processed = 0
    skipped_oversized = 0
    for ch in channels:
        cid = ch["id"]
        route = routes[cid]
        if route is None:
            # Preserve an existing cursor so a temporary rename/unbind does not
            # discard media that should be delivered after the route is fixed.
            continue
        cursor = state["channels"].get(cid)
        try:
            skipped_oversized += process_channel(token, telegram_token, group_id, state, ch, route, cursor) or 0
            processed += 1
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (403, 404):
                print(f"  Skipped {channel_label(ch)}: Discord bot cannot access this channel")
                continue
            raise
    if not processed:
        raise RuntimeError("The Discord bot cannot read any matched channel; allow View Channel and Read Message History")
    if skipped_oversized:
        raise RuntimeError(f"Skipped {skipped_oversized} oversized media attachment(s); other media was processed. No links were posted")
    print("All accessible channels processed")


def process_channel(token: str, telegram_token: str, group_id: str, state: dict, ch: dict, route: str | None, cursor: dict | None):
    cid = ch["id"]
    if cursor is None:
        latest = discord_request(token, f"/channels/{cid}/messages", {"limit": 1})
        state["channels"][cid] = {
            "last_message_id": latest[0]["id"] if latest else "0",
            "last_attachment_index": len(media_attachments(latest[0])) if latest else 0,
        }
        save_state(state)
        print(f"  Initialized {channel_label(ch)}; old history intentionally skipped")
        return 0
    messages = get_new_messages(token, cid, cursor["last_message_id"])
    skipped_oversized = 0
    for message in messages:
        assets = media_attachments(message)
        start = cursor["last_attachment_index"] if message["id"] == cursor["last_message_id"] else 0
        if route:
            for index in range(start, len(assets)):
                try:
                    send_media(telegram_token, group_id, route, assets[index])
                except MediaTooLarge:
                    skipped_oversized += 1
                    print("::warning::An oversized media file was skipped. No replacement link or text was sent to Telegram")
                cursor["last_message_id"] = message["id"]
                cursor["last_attachment_index"] = index + 1
                save_state(state)
        elif assets and message["id"] != cursor["last_message_id"]:
            print(f"  Skipped {len(assets)} attachment(s) in {channel_label(ch)}; no matched topic")
        cursor["last_message_id"] = message["id"]
        cursor["last_attachment_index"] = len(assets)
        save_state(state)
    return skipped_oversized


def main():
    required = ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_GROUP_ID", "TELEGRAM_OWNER_USER_ID")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError("Missing GitHub Actions secrets/configuration: " + ", ".join(missing))
    discord_token = get_secret("DISCORD_BOT_TOKEN")
    guild_id = get_secret("DISCORD_GUILD_ID")
    telegram_token = get_secret("TELEGRAM_BOT_TOKEN")
    group_id = get_secret("TELEGRAM_GROUP_ID")
    owner_id = get_secret("TELEGRAM_OWNER_USER_ID")
    state = load_state()
    config = load_routing_config()
    for name, current in (("discord_guild_id", guild_id), ("telegram_group_id", group_id)):
        if name in config and str(config[name]) != current:
            raise RuntimeError(f"Private routing configuration {name} does not match the connected group")
    state["topics"].update(config["topics"])
    try:
        poll_registration_commands(telegram_token, group_id, owner_id, state)
        if not state["topics"]:
            raise RuntimeError("No Telegram topics registered. Send /bind Exact Topic Name inside each destination topic from your configured owner account")
        process_discord(discord_token, guild_id, telegram_token, group_id, state, overrides=config["channel_overrides"])
    finally:
        save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
