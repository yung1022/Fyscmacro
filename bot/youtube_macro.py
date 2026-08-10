#!/usr/bin/env python3
"""YouTube live chat macro: send a message/command on a fixed interval."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
UA = "fyscmacro-youtube-bot/1.0"


class YouTubeApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"YouTube API HTTP {status}: {body}")


def http_json(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    body: Optional[dict[str, Any]] = None,
    form: Optional[dict[str, str]] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    data: Optional[bytes] = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")
        raise YouTubeApiError(exc.code, err_body) from exc


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    payload = http_json(
        "POST",
        OAUTH_TOKEN_URL,
        form={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"Token refresh failed: {payload}")
    return str(token)


def extract_video_id(value: str) -> Optional[str]:
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"[\w-]{11}", value):
        return value
    patterns = [
        r"(?:youtube\.com/watch\?(?:[^#]*&)?v=|youtu\.be/|youtube\.com/live/|youtube\.com/embed/)([\w-]{11})",
        r"youtube\.com/shorts/([\w-]{11})",
    ]
    for pat in patterns:
        match = re.search(pat, value)
        if match:
            return match.group(1)
    return None


def extract_channel_handle(value: str) -> Optional[str]:
    value = value.strip()
    if not value:
        return None
    if value.startswith("@"):
        return value[1:]
    match = re.search(r"youtube\.com/@([\w.-]+)", value)
    if match:
        return match.group(1)
    return None


def extract_channel_id(value: str) -> Optional[str]:
    value = value.strip()
    if re.fullmatch(r"UC[\w-]{22}", value):
        return value
    match = re.search(r"youtube\.com/channel/(UC[\w-]{22})", value)
    if match:
        return match.group(1)
    return None


def api_get(path: str, access_token: str, params: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{YOUTUBE_API}/{path}?{query}"
    return http_json(
        "GET",
        url,
        headers={"Authorization": f"Bearer {access_token}"},
    )


def resolve_channel_id(access_token: str, channel: str) -> str:
    channel_id = extract_channel_id(channel)
    if channel_id:
        return channel_id
    handle = extract_channel_handle(channel) or channel.lstrip("@")
    data = api_get(
        "channels",
        access_token,
        {"part": "id", "forHandle": handle},
    )
    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"Could not resolve channel handle @{handle}")
    return str(items[0]["id"])


def find_active_live_video_id(access_token: str, channel_id: str) -> str:
    data = api_get(
        "search",
        access_token,
        {
            "part": "snippet",
            "channelId": channel_id,
            "eventType": "live",
            "type": "video",
            "maxResults": "1",
        },
    )
    items = data.get("items") or []
    if not items:
        raise RuntimeError(
            f"No active livestream found for channel {channel_id}. "
            "Pass a live video URL/ID instead."
        )
    return str(items[0]["id"]["videoId"])


def get_live_chat_id(access_token: str, video_id: str) -> str:
    data = api_get(
        "videos",
        access_token,
        {"part": "liveStreamingDetails,snippet", "id": video_id},
    )
    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"Video not found: {video_id}")
    details = items[0].get("liveStreamingDetails") or {}
    chat_id = details.get("activeLiveChatId")
    if not chat_id:
        title = (items[0].get("snippet") or {}).get("title", video_id)
        raise RuntimeError(
            f"No active live chat for video {video_id!r} ({title}). "
            "The stream may be offline or chat may be disabled."
        )
    return str(chat_id)


def resolve_live_chat_id(access_token: str, video: str, channel: str) -> tuple[str, str]:
    """Return (video_id, live_chat_id)."""
    video_id = extract_video_id(video) if video else None
    if not video_id and channel:
        channel_id = resolve_channel_id(access_token, channel)
        video_id = find_active_live_video_id(access_token, channel_id)
    if not video_id:
        raise RuntimeError("Provide --video (URL/ID) or --channel (ID/handle/URL)")
    chat_id = get_live_chat_id(access_token, video_id)
    return video_id, chat_id


def send_live_chat_message(access_token: str, live_chat_id: str, message: str) -> dict[str, Any]:
    message = message.replace("\r", " ").replace("\n", " ").strip()
    if not message:
        raise ValueError("Message/command is empty")
    if len(message) > 200:
        raise ValueError("Message exceeds YouTube live chat length limit (~200)")
    url = f"{YOUTUBE_API}/liveChat/messages?part=snippet"
    return http_json(
        "POST",
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        body={
            "snippet": {
                "liveChatId": live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": message},
            }
        },
    )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a YouTube live chat command/message on a custom interval."
    )
    parser.add_argument(
        "--video",
        default=os.getenv("YOUTUBE_VIDEO", ""),
        help="Live video URL or 11-char video ID (preferred)",
    )
    parser.add_argument(
        "--channel",
        default=os.getenv("YOUTUBE_CHANNEL", ""),
        help="Channel ID, @handle, or channel URL (finds current live)",
    )
    parser.add_argument(
        "--command",
        default=os.getenv("COMMAND", ""),
        help="Chat message or command to send",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("INTERVAL_SECONDS", "60")),
        help="Seconds between sends (default: 60)",
    )
    parser.add_argument(
        "--duration-minutes",
        type=float,
        default=float(os.getenv("DURATION_MINUTES", "30")),
        help="How long to keep sending (default: 30)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.getenv("SEND_COUNT", "0")),
        help="Optional max number of sends (0 = until duration ends)",
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("YOUTUBE_CLIENT_ID", ""),
        help="Google OAuth client ID",
    )
    parser.add_argument(
        "--client-secret",
        default=os.getenv("YOUTUBE_CLIENT_SECRET", ""),
        help="Google OAuth client secret",
    )
    parser.add_argument(
        "--refresh-token",
        default=os.getenv("YOUTUBE_REFRESH_TOKEN", ""),
        help="Google OAuth refresh token",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"},
        help="Log sends without calling YouTube",
    )
    return parser.parse_args(argv)


def validate(args: argparse.Namespace) -> None:
    if not args.command:
        raise SystemExit("Missing --command / COMMAND")
    if not args.video and not args.channel:
        raise SystemExit("Provide --video and/or --channel")
    if args.interval_seconds < 1:
        raise SystemExit("interval-seconds must be >= 1 (avoid spam / rate limits)")
    if args.duration_minutes <= 0 and args.count <= 0:
        raise SystemExit("Set a positive duration-minutes and/or count")
    if not args.dry_run:
        missing = [
            name
            for name, value in (
                ("YOUTUBE_CLIENT_ID", args.client_id),
                ("YOUTUBE_CLIENT_SECRET", args.client_secret),
                ("YOUTUBE_REFRESH_TOKEN", args.refresh_token),
            )
            if not value
        ]
        if missing:
            raise SystemExit(f"Missing OAuth secrets/args: {', '.join(missing)}")


def run(args: argparse.Namespace) -> int:
    validate(args)
    interval = args.interval_seconds
    duration_sec = max(0.0, args.duration_minutes) * 60.0
    end_at = time.monotonic() + duration_sec if duration_sec > 0 else None
    max_count = args.count if args.count > 0 else None

    print(
        f"video={args.video or '(auto)'} channel={args.channel or '(none)'} "
        f"interval={interval}s duration={args.duration_minutes}m "
        f"count={max_count or 'unlimited'} dry_run={args.dry_run}",
        flush=True,
    )
    print(f"Command: {args.command!r}", flush=True)

    access_token: Optional[str] = None
    live_chat_id = "dry-run-chat"
    video_id = extract_video_id(args.video) or "dry-run-video"

    if not args.dry_run:
        access_token = refresh_access_token(
            args.client_id, args.client_secret, args.refresh_token
        )
        video_id, live_chat_id = resolve_live_chat_id(
            access_token, args.video, args.channel
        )
        print(f"Resolved video={video_id} liveChatId={live_chat_id}", flush=True)

    sent = 0
    try:
        while True:
            if end_at is not None and time.monotonic() >= end_at:
                print("Duration reached; stopping", flush=True)
                break
            if max_count is not None and sent >= max_count:
                print("Send count reached; stopping", flush=True)
                break

            if args.dry_run:
                print(
                    f"[dry-run] would send to chat of {video_id}: {args.command}",
                    flush=True,
                )
            else:
                assert access_token is not None
                try:
                    send_live_chat_message(access_token, live_chat_id, args.command)
                except YouTubeApiError as exc:
                    # Access tokens expire ~1h; refresh once and retry.
                    if exc.status == 401:
                        access_token = refresh_access_token(
                            args.client_id, args.client_secret, args.refresh_token
                        )
                        send_live_chat_message(
                            access_token, live_chat_id, args.command
                        )
                    else:
                        raise
                print(f"Sent #{sent + 1}: {args.command}", flush=True)

            sent += 1

            if end_at is not None:
                remaining = end_at - time.monotonic()
                if remaining <= 0:
                    break
                if max_count is not None and sent >= max_count:
                    break
                time.sleep(min(interval, remaining))
            else:
                if max_count is not None and sent >= max_count:
                    break
                time.sleep(interval)
    except KeyboardInterrupt:
        print("Interrupted", flush=True)
        return 130

    print(f"Done. Total sends: {sent}", flush=True)
    return 0


def main() -> None:
    args = parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
