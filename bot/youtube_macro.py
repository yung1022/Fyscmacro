#!/usr/bin/env python3
"""YouTube live chat macro via InnerTube (youtubei)."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import ssl
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

ORIGIN = "https://www.youtube.com"
# Public WEB InnerTube key (also embedded in youtube.com)
DEFAULT_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
DEFAULT_CLIENT_VERSION = "2.20260101.00.00"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class InnerTubeError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(message)


# --- minimal protobuf helpers (masterchat / YouTube.js LiveMessageParams) ---


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n & 0x7F)
    return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _encode_varint((field << 3) | wire)


def pb_ld(field: int, payload: bytes | str | list[bytes]) -> bytes:
    if isinstance(payload, list):
        payload = b"".join(payload)
    elif isinstance(payload, str):
        payload = payload.encode("utf-8")
    return _tag(field, 2) + _encode_varint(len(payload)) + payload


def pb_vt(field: int, value: int) -> bytes:
    return _tag(field, 0) + _encode_varint(value)


def b64_type_b2(payload: bytes) -> str:
    """Double-encoded params used by live_chat/send_message (B64Type.B2)."""
    inner = base64.b64encode(payload).decode("ascii")
    uri = urllib.parse.quote(inner, safe="")
    return base64.b64encode(uri.encode("utf-8")).decode("ascii")


def send_message_params(channel_id: str, video_id: str) -> str:
    """Build InnerTube params for live_chat/send_message."""
    cv_token = pb_ld(5, [pb_ld(1, channel_id), pb_ld(2, video_id)])
    raw = b"".join([pb_ld(1, cv_token), pb_vt(2, 2), pb_vt(3, 4)])
    return b64_type_b2(raw)


# --- cookies / SAPISIDHASH ---


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def pick_sapisid(cookies: dict[str, str]) -> str:
    for key in ("SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID"):
        if cookies.get(key):
            return cookies[key]
    raise InnerTubeError(
        "Cookie string is missing SAPISID / __Secure-3PAPISID "
        "(required to post chat). Copy cookies while logged into YouTube."
    )


def sapisidhash(sapisid: str, origin: str = ORIGIN) -> str:
    ts = int(time.time())
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode("utf-8")).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def auth_headers(cookie_header: str) -> dict[str, str]:
    cookies = parse_cookie_header(cookie_header)
    sapisid = pick_sapisid(cookies)
    return {
        "Cookie": cookie_header.strip(),
        "Authorization": sapisidhash(sapisid),
        "X-Origin": ORIGIN,
        "X-Goog-AuthUser": "0",
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/",
    }


# --- HTTP / InnerTube ---


def http_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: float = 30.0,
) -> tuple[int, str, dict[str, str]]:
    hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    context = ssl.create_default_context()
    # Do not use a shared cookie jar; we pass Cookie explicitly.
    opener = urllib.request.build_opener(
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPSHandler(context=context),
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, raw, resp_headers
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise InnerTubeError(
            f"HTTP {exc.code} for {url}", status=exc.code, body=err_body
        ) from exc


def http_get_text(url: str, headers: Optional[dict[str, str]] = None) -> str:
    _, text, _ = http_request("GET", url, headers=headers)
    return text


def http_get_final_url(url: str, headers: Optional[dict[str, str]] = None) -> str:
    """Follow redirects and return the final URL (for /@handle/live)."""
    class Capture(urllib.request.HTTPRedirectHandler):
        last = url

        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
            Capture.last = urllib.parse.urljoin(req.full_url, newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        Capture(),
        urllib.request.HTTPSHandler(context=context),
    )
    try:
        with opener.open(req, timeout=30) as resp:
            Capture.last = resp.geturl()
            resp.read(1024)
    except urllib.error.HTTPError as exc:
        # Some live redirects still land on a usable URL
        if exc.url:
            return exc.url
        raise InnerTubeError(
            f"HTTP {exc.code} for {url}", status=exc.code, body=""
        ) from exc
    return Capture.last


def extract_ytcfg(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in (
        "INNERTUBE_API_KEY",
        "INNERTUBE_CLIENT_VERSION",
        "INNERTUBE_CLIENT_NAME",
        "DELEGATED_SESSION_ID",
        "SESSION_INDEX",
    ):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', html)
        if match:
            out[key] = match.group(1)
    return out


def extract_json_assignment(html: str, var_name: str) -> Optional[dict[str, Any]]:
    marker = f"var {var_name} = "
    idx = html.find(marker)
    if idx < 0:
        marker = f"window[\"{var_name}\"] = "
        idx = html.find(marker)
    if idx < 0:
        return None
    idx += len(marker)
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[idx:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


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
    if value.startswith("@") and "/" not in value:
        return value[1:]
    match = re.search(r"youtube\.com/@([\w.-]+)", value)
    return match.group(1) if match else None


def extract_channel_id(value: str) -> Optional[str]:
    value = value.strip()
    if re.fullmatch(r"UC[\w-]{22}", value):
        return value
    match = re.search(r"youtube\.com/channel/(UC[\w-]{22})", value)
    return match.group(1) if match else None


class InnerTubeSession:
    def __init__(self, cookie_header: str = "", dry_run: bool = False) -> None:
        self.cookie_header = cookie_header.strip()
        self.dry_run = dry_run
        self.api_key = DEFAULT_API_KEY
        self.client_version = DEFAULT_CLIENT_VERSION
        self.delegated_session_id: Optional[str] = None
        self._bootstrapped = False

    def bootstrap(self) -> None:
        if self._bootstrapped:
            return
        headers = {"Cookie": self.cookie_header} if self.cookie_header else None
        html = http_get_text(f"{ORIGIN}/", headers=headers)
        cfg = extract_ytcfg(html)
        self.api_key = cfg.get("INNERTUBE_API_KEY", self.api_key)
        self.client_version = cfg.get(
            "INNERTUBE_CLIENT_VERSION", self.client_version
        )
        self.delegated_session_id = cfg.get("DELEGATED_SESSION_ID")
        self._bootstrapped = True

    def _context(self) -> dict[str, Any]:
        return {
            "client": {
                "clientName": "WEB",
                "clientVersion": self.client_version,
                "hl": "en",
                "gl": "US",
                "userAgent": USER_AGENT,
            }
        }

    def _headers(self, *, authed: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Youtube-Client-Name": "1",
            "X-Youtube-Client-Version": self.client_version,
        }
        if authed:
            if not self.cookie_header:
                raise InnerTubeError("YOUTUBE_COOKIES is required to send chat")
            headers.update(auth_headers(self.cookie_header))
            if self.delegated_session_id:
                headers["X-Goog-PageId"] = self.delegated_session_id
        elif self.cookie_header:
            headers["Cookie"] = self.cookie_header
        return headers

    def youtubei(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        authed: bool = False,
    ) -> dict[str, Any]:
        self.bootstrap()
        body = {"context": self._context(), **payload}
        url = f"{ORIGIN}/youtubei/v1/{path}?prettyPrint=false&key={self.api_key}"
        status, text, _ = http_request(
            "POST",
            url,
            headers=self._headers(authed=authed),
            body=json.dumps(body).encode("utf-8"),
        )
        if status >= 400:
            raise InnerTubeError(
                f"InnerTube {path} failed with HTTP {status}",
                status=status,
                body=text,
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InnerTubeError(f"Invalid JSON from {path}: {text[:200]}") from exc
        return data

    def resolve_video_from_channel(self, channel: str) -> str:
        handle = extract_channel_handle(channel)
        channel_id = extract_channel_id(channel)
        headers = {"Cookie": self.cookie_header} if self.cookie_header else None
        if handle:
            final = http_get_final_url(f"{ORIGIN}/@{handle}/live", headers=headers)
            video_id = extract_video_id(final)
            if video_id:
                return video_id
            # Fallback: parse live badge from /streams
            html = http_get_text(f"{ORIGIN}/@{handle}/streams", headers=headers)
            video_id = _find_live_video_in_html(html)
            if video_id:
                return video_id
            raise InnerTubeError(
                f"No active livestream found for @{handle}. "
                "Pass a live video URL/ID instead."
            )
        if channel_id:
            final = http_get_final_url(
                f"{ORIGIN}/channel/{channel_id}/live", headers=headers
            )
            video_id = extract_video_id(final)
            if video_id:
                return video_id
            raise InnerTubeError(
                f"No active livestream found for channel {channel_id}."
            )
        # Bare handle without @
        return self.resolve_video_from_channel("@" + channel.lstrip("@"))

    def resolve_ids(self, video: str, channel: str) -> tuple[str, str]:
        video_id = extract_video_id(video) if video else None
        if not video_id and channel:
            video_id = self.resolve_video_from_channel(channel)
        if not video_id:
            raise InnerTubeError("Provide --video (URL/ID) or --channel (@handle)")

        headers = {"Cookie": self.cookie_header} if self.cookie_header else None
        html = http_get_text(f"{ORIGIN}/watch?v={video_id}", headers=headers)
        cfg = extract_ytcfg(html)
        if cfg.get("INNERTUBE_API_KEY"):
            self.api_key = cfg["INNERTUBE_API_KEY"]
        if cfg.get("INNERTUBE_CLIENT_VERSION"):
            self.client_version = cfg["INNERTUBE_CLIENT_VERSION"]
        if cfg.get("DELEGATED_SESSION_ID"):
            self.delegated_session_id = cfg["DELEGATED_SESSION_ID"]
        self._bootstrapped = True

        player = extract_json_assignment(html, "ytInitialPlayerResponse")
        channel_id = None
        is_live = False
        if player:
            details = player.get("videoDetails") or {}
            channel_id = details.get("channelId")
            is_live = bool(details.get("isLive") or details.get("isLiveContent"))
            micro = (player.get("microformat") or {}).get(
                "playerMicroformatRenderer"
            ) or {}
            if not channel_id:
                channel_id = micro.get("externalChannelId")
            live_details = micro.get("liveBroadcastDetails") or {}
            if live_details.get("isLiveNow"):
                is_live = True

        if not channel_id:
            # InnerTube player fallback
            data = self.youtubei("player", {"videoId": video_id}, authed=False)
            details = data.get("videoDetails") or {}
            channel_id = details.get("channelId")
            is_live = bool(details.get("isLive") or details.get("isLiveContent"))

        if not channel_id:
            raise InnerTubeError(f"Could not resolve channelId for video {video_id}")
        if not is_live:
            # Still try to send — some premieres report oddly; warn only.
            print(
                f"Warning: video {video_id} may not be live right now",
                flush=True,
            )
        return video_id, str(channel_id)

    def send_message(self, channel_id: str, video_id: str, message: str) -> dict[str, Any]:
        message = message.replace("\r", " ").replace("\n", " ").strip()
        if not message:
            raise ValueError("Message/command is empty")
        if len(message) > 200:
            raise ValueError("Message exceeds typical YouTube live chat length (~200)")

        payload = {
            "params": send_message_params(channel_id, video_id),
            "richMessage": {"textSegments": [{"text": message}]},
            "clientMessageId": str(uuid.uuid4()),
        }
        data = self.youtubei("live_chat/send_message", payload, authed=True)
        if data.get("timeoutDurationUsec"):
            usec = int(data["timeoutDurationUsec"])
            raise InnerTubeError(
                f"Account timed out from chat for ~{usec // 1_000_000}s"
            )
        actions = data.get("actions") or []
        if not actions:
            raise InnerTubeError(
                "Send failed (empty actions). Cookies may be expired, "
                "chat may be disabled, or params invalid.",
                body=json.dumps(data)[:800],
            )
        # Error toast: "Error, try again."
        toast = json.dumps(actions)
        if "Error, try again" in toast or "liveChatAddToToastAction" in toast:
            if "liveChatTextMessageRenderer" not in toast:
                raise InnerTubeError(
                    "Send rejected by YouTube (toast error). "
                    "Check cookies and that the stream chat is open.",
                    body=toast[:800],
                )
        return data


def _find_live_video_in_html(html: str) -> Optional[str]:
    # Prefer badges marked LIVE on /streams shelf
    for match in re.finditer(
        r'"videoId":"([\w-]{11})"[^}]{0,400}?"style":"LIVE"', html
    ):
        return match.group(1)
    for match in re.finditer(
        r'"style":"LIVE"[^}]{0,400}?"videoId":"([\w-]{11})"', html
    ):
        return match.group(1)
    return None


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a YouTube live chat command via InnerTube on an interval."
    )
    parser.add_argument(
        "--video",
        default=os.getenv("YOUTUBE_VIDEO", ""),
        help="Live video URL or 11-char video ID (preferred)",
    )
    parser.add_argument(
        "--channel",
        default=os.getenv("YOUTUBE_CHANNEL", ""),
        help="Channel @handle, ID, or URL (finds current /live)",
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
        "--cookies",
        default=os.getenv("YOUTUBE_COOKIES", ""),
        help="Logged-in YouTube Cookie header value",
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
    if not args.dry_run and not args.cookies:
        raise SystemExit("Missing --cookies / YOUTUBE_COOKIES")


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

    session = InnerTubeSession(cookie_header=args.cookies, dry_run=args.dry_run)
    video_id = extract_video_id(args.video) or "dry-run-video"
    channel_id = "dry-run-channel"

    if not args.dry_run:
        video_id, channel_id = session.resolve_ids(args.video, args.channel)
        print(f"Resolved video={video_id} channel={channel_id}", flush=True)

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
                params = send_message_params("UCabcdefghijklmnopqrstuv", video_id)
                print(
                    f"[dry-run] would InnerTube send to {video_id}: {args.command} "
                    f"(params_len={len(params)})",
                    flush=True,
                )
            else:
                session.send_message(channel_id, video_id, args.command)
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
    except InnerTubeError as exc:
        print(f"Error: {exc}", flush=True)
        if exc.body:
            print(exc.body[:1000], flush=True)
        return 1

    print(f"Done. Total sends: {sent}", flush=True)
    return 0


def main() -> None:
    args = parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
