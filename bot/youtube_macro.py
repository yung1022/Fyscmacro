#!/usr/bin/env python3
"""YouTube live chat macro via InnerTube (cookie + SAPISIDHASH auth).

YouTube blocked TV device-OAuth for live-chat send and for the public Data API
(403/400). Sending requires a logged-in browser Cookie header (works from
phone with Kiwi Browser DevTools — no desktop required).
"""

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
DEFAULT_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
DEFAULT_CLIENT_VERSION = "2.20260101.00.00"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)


class BotError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(message)


# --- protobuf (InnerTube send_message params) ---


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
    inner = base64.b64encode(payload).decode("ascii")
    uri = urllib.parse.quote(inner, safe="")
    return base64.b64encode(uri.encode("utf-8")).decode("ascii")


def send_message_params(channel_id: str, video_id: str) -> str:
    """YouTube.js LiveMessageParams shape."""
    cv_token = pb_ld(5, [pb_ld(1, channel_id), pb_ld(2, video_id)])
    raw = b"".join([pb_ld(1, cv_token), pb_vt(2, 1), pb_vt(3, 4)])
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
    raise BotError(
        "YOUTUBE_COOKIES is missing SAPISID / __Secure-3PAPISID. "
        "Copy the full Cookie header while logged into YouTube."
    )


def sapisidhash(sapisid: str, origin: str = ORIGIN) -> str:
    ts = int(time.time())
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode("utf-8")).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def cookie_auth_headers(cookie_header: str) -> dict[str, str]:
    cookies = parse_cookie_header(cookie_header)
    sapisid = pick_sapisid(cookies)
    return {
        "Cookie": cookie_header.strip(),
        "Authorization": sapisidhash(sapisid),
        "X-Origin": ORIGIN,
        "Origin": ORIGIN,
        "X-Goog-AuthUser": "0",
        "Referer": f"{ORIGIN}/",
    }


# --- HTTP ---


def http_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: float = 30.0,
) -> tuple[int, str]:
    hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise BotError(
            f"HTTP {exc.code} for {url}", status=exc.code, body=err_body
        ) from exc


def http_get_text(url: str, headers: Optional[dict[str, str]] = None) -> str:
    _, text = http_request("GET", url, headers=headers)
    return text


def http_get_final_url(url: str, headers: Optional[dict[str, str]] = None) -> str:
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
        if getattr(exc, "url", None):
            return str(exc.url)
        raise BotError(f"HTTP {exc.code} for {url}", status=exc.code) from exc
    return Capture.last


def http_json(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    body: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    hdrs = {"Accept": "application/json"}
    data: Optional[bytes] = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    status, text = http_request(method, url, headers=hdrs, body=data)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise BotError(f"Invalid JSON from {url}: {text[:200]}") from exc


# --- parsing ---


def extract_ytcfg(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in (
        "INNERTUBE_API_KEY",
        "INNERTUBE_CLIENT_VERSION",
        "DELEGATED_SESSION_ID",
        "VISITOR_DATA",
    ):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', html)
        if match:
            out[key] = match.group(1)
    # visitorData often nested differently
    if "VISITOR_DATA" not in out:
        match = re.search(r'"visitorData"\s*:\s*"([^"]+)"', html)
        if match:
            out["VISITOR_DATA"] = match.group(1)
    return out


def extract_json_assignment(html: str, var_name: str) -> Optional[dict[str, Any]]:
    for marker in (f"var {var_name} = ", f'window["{var_name}"] = '):
        idx = html.find(marker)
        if idx >= 0:
            idx += len(marker)
            try:
                payload, _ = json.JSONDecoder().raw_decode(html[idx:])
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


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


def _find_live_video_in_html(html: str) -> Optional[str]:
    for match in re.finditer(
        r'"videoId":"([\w-]{11})"[^}]{0,400}?"style":"LIVE"', html
    ):
        return match.group(1)
    for match in re.finditer(
        r'"style":"LIVE"[^}]{0,400}?"videoId":"([\w-]{11})"', html
    ):
        return match.group(1)
    return None


def _channel_id_from_owner_renderer(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    owner = obj.get("videoOwnerRenderer")
    if not isinstance(owner, dict):
        return None
    cid = owner.get("channelId")
    if isinstance(cid, str) and cid.startswith("UC"):
        return cid
    nav = owner.get("navigationEndpoint")
    if isinstance(nav, dict):
        browse = (nav.get("browseEndpoint") or {}).get("browseId")
        if isinstance(browse, str) and browse.startswith("UC"):
            return browse
    return None


def _walk_find_video_owner_channel_id(obj: Any) -> Optional[str]:
    found = _channel_id_from_owner_renderer(obj)
    if found:
        return found
    if isinstance(obj, dict):
        for value in obj.values():
            found = _walk_find_video_owner_channel_id(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _walk_find_video_owner_channel_id(item)
            if found:
                return found
    return None


def extract_channel_id_from_watch_html(
    html: str, video_id: str = ""
) -> Optional[str]:
    player = extract_json_assignment(html, "ytInitialPlayerResponse")
    if player:
        details = player.get("videoDetails") or {}
        cid = details.get("channelId")
        if cid:
            return str(cid)
        micro = (player.get("microformat") or {}).get(
            "playerMicroformatRenderer"
        ) or {}
        cid = micro.get("externalChannelId")
        if cid:
            return str(cid)

    initial = extract_json_assignment(html, "ytInitialData")
    if initial:
        cid = _walk_find_video_owner_channel_id(initial)
        if cid:
            return cid

    owner_match = re.search(
        r'"videoOwnerRenderer"[\s\S]{0,2500}?"channelId"\s*:\s*"(UC[\w-]{22})"',
        html,
    )
    if owner_match:
        return owner_match.group(1)

    if video_id:
        near_video = re.search(
            rf'"videoId"\s*:\s*"{re.escape(video_id)}"[\s\S]{{0,5000}}?'
            r'"channelId"\s*:\s*"(UC[\w-]{22})"',
            html,
        )
        if near_video:
            return near_video.group(1)
    return None


def extract_live_chat_continuation(html: str) -> Optional[str]:
    """Pull the live chat reload continuation from the watch page."""
    initial = extract_json_assignment(html, "ytInitialData")
    if initial:
        blob = json.dumps(initial)
        # conversationBar liveChatRenderer continuations
        match = re.search(
            r'"liveChatRenderer"[\s\S]{0,2000}?"reloadContinuationData"\s*:\s*\{\s*"continuation"\s*:\s*"([^"]+)"',
            blob,
        )
        if match:
            return match.group(1)
        match = re.search(
            r'"liveChatRenderer"[\s\S]{0,2000}?"continuation"\s*:\s*"([^"]+)"',
            blob,
        )
        if match:
            return match.group(1)

    match = re.search(
        r'"liveChatRenderer"[\s\S]{0,2000}?"continuation"\s*:\s*"([^"]+)"',
        html,
    )
    return match.group(1) if match else None


def gh_notice(title: str, message: str) -> None:
    safe_title = title.replace("\n", " ").replace("%", "%25").replace(":", "%3A")
    safe_msg = message.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::notice title={safe_title}::{safe_msg}", flush=True)


def gh_error(message: str) -> None:
    safe = message.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error::{safe}", flush=True)


# --- InnerTube session ---


class YouTubeMacro:
    def __init__(self, cookie_header: str = "") -> None:
        self.cookie_header = cookie_header.strip()
        self.api_key = DEFAULT_API_KEY
        self.client_version = DEFAULT_CLIENT_VERSION
        self.visitor_data: Optional[str] = None
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
        self.visitor_data = cfg.get("VISITOR_DATA")
        self.delegated_session_id = cfg.get("DELEGATED_SESSION_ID")
        self._bootstrapped = True

    def _headers(self, *, authed: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Youtube-Client-Name": "1",
            "X-Youtube-Client-Version": self.client_version,
            "Origin": ORIGIN,
            "Referer": f"{ORIGIN}/",
        }
        if authed:
            if not self.cookie_header:
                raise BotError("YOUTUBE_COOKIES is required to send chat")
            headers.update(cookie_auth_headers(self.cookie_header))
            if self.delegated_session_id:
                headers["X-Goog-PageId"] = self.delegated_session_id
        elif self.cookie_header:
            headers["Cookie"] = self.cookie_header
        return headers

    def innertube(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        authed: bool = False,
    ) -> dict[str, Any]:
        self.bootstrap()
        client: dict[str, Any] = {
            "clientName": "WEB",
            "clientVersion": self.client_version,
            "hl": "en",
            "gl": "US",
            "userAgent": USER_AGENT,
        }
        if self.visitor_data:
            client["visitorData"] = self.visitor_data
        body = {"context": {"client": client}, **payload}
        url = f"{ORIGIN}/youtubei/v1/{path}?prettyPrint=false&key={self.api_key}"
        status, text = http_request(
            "POST",
            url,
            headers=self._headers(authed=authed),
            body=json.dumps(body).encode("utf-8"),
        )
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise BotError(f"Invalid InnerTube JSON ({status}): {text[:200]}") from exc
        if isinstance(data, dict) and data.get("error"):
            raise BotError(
                f"InnerTube error on {path}",
                status=status,
                body=json.dumps(data)[:800],
            )
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
            html = http_get_text(f"{ORIGIN}/@{handle}/streams", headers=headers)
            video_id = _find_live_video_in_html(html)
            if video_id:
                return video_id
            raise BotError(f"No active livestream for @{handle}")
        if channel_id:
            final = http_get_final_url(
                f"{ORIGIN}/channel/{channel_id}/live", headers=headers
            )
            video_id = extract_video_id(final)
            if video_id:
                return video_id
            raise BotError(f"No active livestream for {channel_id}")
        return self.resolve_video_from_channel("@" + channel.lstrip("@"))

    def resolve_channel_id(self, channel: str) -> Optional[str]:
        cid = extract_channel_id(channel)
        if cid:
            return cid
        handle = extract_channel_handle(channel) or channel.lstrip("@").strip()
        if not handle:
            return None
        data = self.innertube(
            "navigation/resolve_url",
            {"url": f"{ORIGIN}/@{handle.lstrip('@')}"},
            authed=False,
        )
        browse = (data.get("endpoint") or {}).get("browseEndpoint") or {}
        bid = browse.get("browseId")
        return str(bid) if bid else None

    def resolve_ids(self, video: str, channel: str) -> tuple[str, str, str]:
        video_id = extract_video_id(video) if video else None
        if not video_id and channel:
            video_id = self.resolve_video_from_channel(channel)
        if not video_id:
            raise BotError("Provide --video or --channel")

        headers = {"Cookie": self.cookie_header} if self.cookie_header else None
        html = http_get_text(f"{ORIGIN}/watch?v={video_id}", headers=headers)
        cfg = extract_ytcfg(html)
        if cfg.get("INNERTUBE_API_KEY"):
            self.api_key = cfg["INNERTUBE_API_KEY"]
        if cfg.get("INNERTUBE_CLIENT_VERSION"):
            self.client_version = cfg["INNERTUBE_CLIENT_VERSION"]
        if cfg.get("VISITOR_DATA"):
            self.visitor_data = cfg["VISITOR_DATA"]
        if cfg.get("DELEGATED_SESSION_ID"):
            self.delegated_session_id = cfg["DELEGATED_SESSION_ID"]
        self._bootstrapped = True

        channel_id = extract_channel_id_from_watch_html(html, video_id)
        if not channel_id and channel:
            channel_id = self.resolve_channel_id(channel)
        if not channel_id:
            # oEmbed → handle
            try:
                oembed = http_json(
                    "GET",
                    "https://www.youtube.com/oembed?"
                    + urllib.parse.urlencode(
                        {
                            "url": f"{ORIGIN}/watch?v={video_id}",
                            "format": "json",
                        }
                    ),
                )
                handle = extract_channel_handle(str(oembed.get("author_url") or ""))
                if handle:
                    channel_id = self.resolve_channel_id("@" + handle)
            except BotError:
                pass
        if not channel_id:
            raise BotError(
                f"Could not resolve channelId for {video_id}. "
                "Pass channel=@handle as well."
            )
        return video_id, str(channel_id), html

    def send_message(self, channel_id: str, video_id: str, message: str) -> dict[str, Any]:
        message = message.replace("\r", " ").replace("\n", " ").strip()
        if not message:
            raise ValueError("empty message")
        if len(message) > 200:
            raise ValueError("message too long (>200)")

        payload = {
            "params": send_message_params(channel_id, video_id),
            "richMessage": {"textSegments": [{"text": message}]},
            "clientMessageId": str(uuid.uuid4()),
        }
        data = self.innertube("live_chat/send_message", payload, authed=True)
        if data.get("timeoutDurationUsec"):
            usec = int(data["timeoutDurationUsec"])
            raise BotError(f"Timed out from chat for ~{usec // 1_000_000}s")
        actions = data.get("actions") or []
        if not actions:
            raise BotError(
                "Send returned no actions. Cookies may be expired, chat may be "
                "closed, or the account cannot post in this chat.",
                body=json.dumps(data)[:800],
            )
        toast = json.dumps(actions)
        if "Error, try again" in toast and "liveChatTextMessageRenderer" not in toast:
            raise BotError(
                "Send rejected by YouTube. Refresh YOUTUBE_COOKIES and retry.",
                body=toast[:800],
            )
        if "RunAttestationCommand" in toast and "liveChatTextMessageRenderer" not in toast:
            print(
                "Warning: YouTube returned an attestation challenge; "
                "message may not have posted.",
                flush=True,
            )
        return data


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send a YouTube live chat command on an interval using "
            "InnerTube + browser cookies (phone-friendly)."
        )
    )
    parser.add_argument("--video", default=os.getenv("YOUTUBE_VIDEO", ""))
    parser.add_argument("--channel", default=os.getenv("YOUTUBE_CHANNEL", ""))
    parser.add_argument("--command", default=os.getenv("COMMAND", ""))
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("INTERVAL_SECONDS", "60")),
    )
    parser.add_argument(
        "--duration-minutes",
        type=float,
        default=float(os.getenv("DURATION_MINUTES", "30")),
    )
    parser.add_argument(
        "--count", type=int, default=int(os.getenv("SEND_COUNT", "0"))
    )
    parser.add_argument(
        "--cookies",
        default=os.getenv("YOUTUBE_COOKIES", ""),
        help="Logged-in YouTube Cookie header (required to send)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"},
    )
    return parser.parse_args(argv)


def validate(args: argparse.Namespace) -> None:
    if not args.command:
        raise SystemExit("Missing --command / COMMAND")
    if not args.video and not args.channel:
        raise SystemExit("Provide --video and/or --channel")
    if args.interval_seconds < 1:
        raise SystemExit("interval-seconds must be >= 1")
    if args.duration_minutes <= 0 and args.count <= 0:
        raise SystemExit("Set a positive duration-minutes and/or count")
    if not args.dry_run:
        if not args.cookies:
            gh_error(
                "Missing YOUTUBE_COOKIES. Phone OAuth can no longer send live "
                "chat (YouTube blocks it). Add the Cookie header from a logged-in "
                "YouTube session (Kiwi Browser on Android works)."
            )
            raise SystemExit("Missing --cookies / YOUTUBE_COOKIES")
        try:
            pick_sapisid(parse_cookie_header(args.cookies))
        except BotError as exc:
            gh_error(str(exc))
            raise SystemExit(str(exc)) from exc


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

    bot = YouTubeMacro(cookie_header=args.cookies)
    video_id = extract_video_id(args.video) or "dry-run-video"
    channel_id = "dry-run-channel"

    if not args.dry_run:
        video_id, channel_id, _html = bot.resolve_ids(args.video, args.channel)
        print(f"Resolved video={video_id} channel={channel_id}", flush=True)
        cont = extract_live_chat_continuation(_html)
        if cont:
            print("Live chat continuation found on watch page", flush=True)
        else:
            print(
                "Warning: no live chat continuation on page — stream may be offline",
                flush=True,
            )

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
                    f"[dry-run] would InnerTube send to {video_id}: {args.command}",
                    flush=True,
                )
            else:
                bot.send_message(channel_id, video_id, args.command)
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
    except BotError as exc:
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
