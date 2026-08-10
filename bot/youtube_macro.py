#!/usr/bin/env python3
"""YouTube live chat macro: InnerTube discovery + Data API / InnerTube send.

Auth is phone-friendly device OAuth (YouTube TV client) — no desktop cookies
and no required GitHub secrets. Open the printed URL on your phone, enter the
code, and the workflow continues.
"""

from __future__ import annotations

import argparse
import base64
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
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
DEFAULT_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
DEFAULT_CLIENT_VERSION = "2.20260101.00.00"
TV_CLIENT_VERSION = "7.20260311.12.00"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
TV_USER_AGENT = "Mozilla/5.0 (ChromiumStylePlatform) Cobalt/Version"

# Widely used YouTube TV OAuth client (fallback if /tv scrape fails)
FALLBACK_TV_CLIENT_ID = (
    "861556708454-d6dlm3lh05idd8npek18k6be8ba3oc68.apps.googleusercontent.com"
)
FALLBACK_TV_CLIENT_SECRET = "SboVhoG9s0rNafixCJ7RIGRR"


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
    cv_token = pb_ld(5, [pb_ld(1, channel_id), pb_ld(2, video_id)])
    raw = b"".join([pb_ld(1, cv_token), pb_vt(2, 2), pb_vt(3, 4)])
    return b64_type_b2(raw)


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
    form: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    hdrs = {"Accept": "application/json"}
    data: Optional[bytes] = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
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


# --- parsing helpers ---


def extract_ytcfg(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in (
        "INNERTUBE_API_KEY",
        "INNERTUBE_CLIENT_VERSION",
        "DELEGATED_SESSION_ID",
    ):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', html)
        if match:
            out[key] = match.group(1)
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


def gh_notice(title: str, message: str) -> None:
    # GitHub Actions annotation (safe characters only)
    safe_title = title.replace("\n", " ").replace("%", "%25").replace(":", "%3A")
    safe_msg = message.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::notice title={safe_title}::{safe_msg}", flush=True)


# --- OAuth device flow (phone) ---


def scrape_tv_oauth_client() -> tuple[str, str]:
    """Pull client_id/secret from YouTube TV JS (same approach as YouTube.js)."""
    html = http_get_text(
        f"{ORIGIN}/tv",
        headers={"User-Agent": TV_USER_AGENT, "Referer": f"{ORIGIN}/tv"},
    )
    script = re.search(
        r'<script[^>]+id="base-js"[^>]+src="([^"]+)"', html
    ) or re.search(r'src="(/s/player/[^"]+/base.js)"', html)
    if not script:
        # Newer TV pages may reference base.js differently
        script = re.search(r'src="(https://www\.youtube\.com/s/[^"]+base\.js)"', html)
    if not script:
        print("TV base.js not found; using fallback OAuth client", flush=True)
        return FALLBACK_TV_CLIENT_ID, FALLBACK_TV_CLIENT_SECRET

    src = script.group(1)
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = ORIGIN + src
    js = http_get_text(src, headers={"User-Agent": TV_USER_AGENT})
    match = re.search(
        r'clientId:"(?P<client_id>[^"]+)",[^"]*?:"(?P<client_secret>[^"]+)"',
        js,
    )
    if not match:
        print("TV client identity not found in JS; using fallback", flush=True)
        return FALLBACK_TV_CLIENT_ID, FALLBACK_TV_CLIENT_SECRET
    return match.group("client_id"), match.group("client_secret")


def device_login(
    *,
    client_id: str,
    client_secret: str,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    """YouTube TV device OAuth — authorize on your phone, no desktop needed."""
    code_url = f"{ORIGIN}/o/oauth2/device/code"
    token_url = f"{ORIGIN}/o/oauth2/token"
    payload = {
        "client_id": client_id,
        "scope": (
            "http://gdata.youtube.com "
            "https://www.googleapis.com/auth/youtube "
            "https://www.googleapis.com/auth/youtube.force-ssl "
            "https://www.googleapis.com/auth/youtube-paid-content"
        ),
        "device_id": str(uuid.uuid4()).replace("-", ""),
        "device_model": "ytlr::",
    }
    data = http_json("POST", code_url, body=payload)
    if data.get("error") or data.get("error_code"):
        raise BotError(f"Device code request failed: {data}")

    user_code = data["user_code"]
    device_code = data["device_code"]
    interval = float(data.get("interval") or 5)
    verification_url = data.get("verification_url") or "https://www.google.com/device"
    expires_in = float(data.get("expires_in") or 1800)
    wait_for = min(timeout_seconds, expires_in - 5)

    print("", flush=True)
    print("=" * 60, flush=True)
    print("  PHONE LOGIN REQUIRED (no desktop / no cookies)", flush=True)
    print(f"  1) Open: {verification_url}", flush=True)
    print(f"  2) Enter code: {user_code}", flush=True)
    print("  3) Approve access with your YouTube account", flush=True)
    print("=" * 60, flush=True)
    print("", flush=True)
    gh_notice(
        "Phone login",
        f"Open {verification_url} and enter code {user_code}",
    )

    deadline = time.monotonic() + wait_for
    token_body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": device_code,
        "grant_type": "http://oauth.net/grant_type/device/1.0",
    }
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            status, text = http_request(
                "POST",
                token_url,
                headers={"Content-Type": "application/json"},
                body=json.dumps(token_body).encode("utf-8"),
            )
            resp = json.loads(text) if text else {}
        except BotError as exc:
            if exc.status in {428, 403}:  # authorization_pending variants
                print("Waiting for phone approval...", flush=True)
                continue
            # Some servers return 400 with JSON error
            try:
                resp = json.loads(exc.body)
            except Exception:
                raise

        err = resp.get("error")
        if err in {"authorization_pending", "slow_down"}:
            if err == "slow_down":
                interval += 2
            print("Waiting for phone approval...", flush=True)
            continue
        if err:
            raise BotError(f"Device login failed: {resp}")
        if resp.get("access_token"):
            print("Phone login successful.", flush=True)
            if resp.get("refresh_token"):
                print(
                    "Optional: save this refresh token as secret "
                    "YOUTUBE_REFRESH_TOKEN for unattended runs:",
                    flush=True,
                )
                # Do not fail if missing — TV flow sometimes omits on re-auth
                print(resp["refresh_token"], flush=True)
            return {
                "access_token": resp["access_token"],
                "refresh_token": resp.get("refresh_token", ""),
                "client_id": client_id,
                "client_secret": client_secret,
                "expires_at": time.time() + float(resp.get("expires_in") or 3600),
            }

    raise BotError("Timed out waiting for phone login")


def refresh_access_token(
    client_id: str, client_secret: str, refresh_token: str
) -> dict[str, Any]:
    token_url = f"{ORIGIN}/o/oauth2/token"
    resp = http_json(
        "POST",
        token_url,
        body={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    if not resp.get("access_token"):
        # Try Google's token endpoint as fallback
        resp = http_json(
            "POST",
            "https://oauth2.googleapis.com/token",
            form={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if not resp.get("access_token"):
        raise BotError(f"Refresh failed: {resp}")
    return {
        "access_token": resp["access_token"],
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "expires_at": time.time() + float(resp.get("expires_in") or 3600),
    }


def obtain_auth(args: argparse.Namespace) -> dict[str, Any]:
    client_id = args.client_id or os.getenv("YOUTUBE_CLIENT_ID", "")
    client_secret = args.client_secret or os.getenv("YOUTUBE_CLIENT_SECRET", "")
    refresh = args.refresh_token or os.getenv("YOUTUBE_REFRESH_TOKEN", "")

    if not client_id or not client_secret:
        client_id, client_secret = scrape_tv_oauth_client()
        print(f"Using TV OAuth client: {client_id[:24]}...", flush=True)

    if refresh:
        print("Using saved refresh token (no phone login this run)", flush=True)
        return refresh_access_token(client_id, client_secret, refresh)

    return device_login(
        client_id=client_id,
        client_secret=client_secret,
        timeout_seconds=args.auth_timeout_seconds,
    )


# --- InnerTube + Data API ---


class YouTubeMacro:
    def __init__(self, auth: Optional[dict[str, Any]] = None) -> None:
        self.auth = auth
        self.api_key = DEFAULT_API_KEY
        self.client_version = DEFAULT_CLIENT_VERSION
        self._bootstrapped = False

    @property
    def access_token(self) -> str:
        if not self.auth or not self.auth.get("access_token"):
            raise BotError("Not authenticated")
        if self.auth.get("expires_at", 0) < time.time() + 60:
            refresh = self.auth.get("refresh_token") or ""
            if not refresh:
                raise BotError(
                    "Access token expired and no refresh token is available. "
                    "Re-run the workflow and complete phone login again."
                )
            self.auth = refresh_access_token(
                self.auth["client_id"],
                self.auth["client_secret"],
                refresh,
            )
        return str(self.auth["access_token"])

    def bootstrap(self) -> None:
        if self._bootstrapped:
            return
        html = http_get_text(f"{ORIGIN}/")
        cfg = extract_ytcfg(html)
        self.api_key = cfg.get("INNERTUBE_API_KEY", self.api_key)
        self.client_version = cfg.get(
            "INNERTUBE_CLIENT_VERSION", self.client_version
        )
        self._bootstrapped = True

    def innertube(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        authed: bool = False,
        tv_client: bool = False,
    ) -> dict[str, Any]:
        self.bootstrap()
        if tv_client:
            client = {
                "clientName": "TVHTML5",
                "clientVersion": TV_CLIENT_VERSION,
                "hl": "en",
                "gl": "US",
                "userAgent": TV_USER_AGENT,
            }
        else:
            client = {
                "clientName": "WEB",
                "clientVersion": self.client_version,
                "hl": "en",
                "gl": "US",
                "userAgent": USER_AGENT,
            }
        body = {"context": {"client": client}, **payload}
        url = f"{ORIGIN}/youtubei/v1/{path}?prettyPrint=false&key={self.api_key}"
        headers = {
            "Content-Type": "application/json",
            "X-Youtube-Client-Name": "7" if tv_client else "1",
            "X-Youtube-Client-Version": (
                TV_CLIENT_VERSION if tv_client else self.client_version
            ),
        }
        if authed:
            headers["Authorization"] = f"Bearer {self.access_token}"
            headers["X-Origin"] = ORIGIN
        status, text = http_request(
            "POST", url, headers=headers, body=json.dumps(body).encode("utf-8")
        )
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise BotError(f"Invalid InnerTube JSON ({status}): {text[:200]}") from exc

    def resolve_video_from_channel(self, channel: str) -> str:
        handle = extract_channel_handle(channel)
        channel_id = extract_channel_id(channel)
        if handle:
            final = http_get_final_url(f"{ORIGIN}/@{handle}/live")
            video_id = extract_video_id(final)
            if video_id:
                return video_id
            html = http_get_text(f"{ORIGIN}/@{handle}/streams")
            video_id = _find_live_video_in_html(html)
            if video_id:
                return video_id
            raise BotError(f"No active livestream for @{handle}")
        if channel_id:
            final = http_get_final_url(f"{ORIGIN}/channel/{channel_id}/live")
            video_id = extract_video_id(final)
            if video_id:
                return video_id
            raise BotError(f"No active livestream for {channel_id}")
        return self.resolve_video_from_channel("@" + channel.lstrip("@"))

    def resolve_ids(self, video: str, channel: str) -> tuple[str, str]:
        video_id = extract_video_id(video) if video else None
        if not video_id and channel:
            video_id = self.resolve_video_from_channel(channel)
        if not video_id:
            raise BotError("Provide --video or --channel")

        html = http_get_text(f"{ORIGIN}/watch?v={video_id}")
        cfg = extract_ytcfg(html)
        if cfg.get("INNERTUBE_API_KEY"):
            self.api_key = cfg["INNERTUBE_API_KEY"]
        if cfg.get("INNERTUBE_CLIENT_VERSION"):
            self.client_version = cfg["INNERTUBE_CLIENT_VERSION"]
        self._bootstrapped = True

        channel_id = None
        player = extract_json_assignment(html, "ytInitialPlayerResponse")
        if player:
            details = player.get("videoDetails") or {}
            channel_id = details.get("channelId")
            micro = (player.get("microformat") or {}).get(
                "playerMicroformatRenderer"
            ) or {}
            channel_id = channel_id or micro.get("externalChannelId")

        if not channel_id:
            data = self.innertube("player", {"videoId": video_id}, authed=False)
            channel_id = (data.get("videoDetails") or {}).get("channelId")

        if not channel_id:
            raise BotError(f"Could not resolve channelId for {video_id}")
        return video_id, str(channel_id)

    def get_live_chat_id(self, video_id: str) -> str:
        """Data API: videos.list → activeLiveChatId."""
        url = (
            f"{YOUTUBE_API}/videos?"
            + urllib.parse.urlencode(
                {"part": "liveStreamingDetails,snippet", "id": video_id}
            )
        )
        data = http_json(
            "GET",
            url,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        items = data.get("items") or []
        if not items:
            raise BotError(f"Video not found via Data API: {video_id}", body=str(data))
        details = items[0].get("liveStreamingDetails") or {}
        chat_id = details.get("activeLiveChatId")
        if not chat_id:
            title = (items[0].get("snippet") or {}).get("title", video_id)
            raise BotError(
                f"No active live chat for {video_id!r} ({title}). Is it live?"
            )
        return str(chat_id)

    def send_via_data_api(self, live_chat_id: str, message: str) -> dict[str, Any]:
        url = f"{YOUTUBE_API}/liveChat/messages?part=snippet"
        return http_json(
            "POST",
            url,
            headers={"Authorization": f"Bearer {self.access_token}"},
            body={
                "snippet": {
                    "liveChatId": live_chat_id,
                    "type": "textMessageEvent",
                    "textMessageDetails": {"messageText": message},
                }
            },
        )

    def send_via_innertube(
        self, channel_id: str, video_id: str, message: str
    ) -> dict[str, Any]:
        payload = {
            "params": send_message_params(channel_id, video_id),
            "richMessage": {"textSegments": [{"text": message}]},
            "clientMessageId": str(uuid.uuid4()),
        }
        data = self.innertube(
            "live_chat/send_message",
            payload,
            authed=True,
            tv_client=True,
        )
        if data.get("timeoutDurationUsec"):
            usec = int(data["timeoutDurationUsec"])
            raise BotError(f"Timed out from chat for ~{usec // 1_000_000}s")
        actions = data.get("actions") or []
        if not actions:
            raise BotError(
                "InnerTube send returned no actions",
                body=json.dumps(data)[:800],
            )
        toast = json.dumps(actions)
        if "Error, try again" in toast and "liveChatTextMessageRenderer" not in toast:
            raise BotError("InnerTube send rejected", body=toast[:800])
        return data

    def send_message(
        self,
        *,
        channel_id: str,
        video_id: str,
        live_chat_id: Optional[str],
        message: str,
    ) -> str:
        message = message.replace("\r", " ").replace("\n", " ").strip()
        if not message:
            raise ValueError("empty message")
        if len(message) > 200:
            raise ValueError("message too long (>200)")

        # Prefer Data API; fall back to InnerTube TV send.
        if live_chat_id:
            try:
                self.send_via_data_api(live_chat_id, message)
                return "data_api"
            except BotError as exc:
                print(f"Data API send failed ({exc}); trying InnerTube...", flush=True)

        self.send_via_innertube(channel_id, video_id, message)
        return "innertube"


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send a YouTube live chat command on an interval. "
            "Uses phone device login by default (no secrets required)."
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
        "--auth-timeout-seconds",
        type=float,
        default=float(os.getenv("AUTH_TIMEOUT_SECONDS", "600")),
        help="How long to wait for phone device login",
    )
    parser.add_argument(
        "--client-id", default=os.getenv("YOUTUBE_CLIENT_ID", "")
    )
    parser.add_argument(
        "--client-secret", default=os.getenv("YOUTUBE_CLIENT_SECRET", "")
    )
    parser.add_argument(
        "--refresh-token", default=os.getenv("YOUTUBE_REFRESH_TOKEN", "")
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

    bot = YouTubeMacro()
    video_id = extract_video_id(args.video) or "dry-run-video"
    channel_id = "dry-run-channel"
    live_chat_id: Optional[str] = None

    if not args.dry_run:
        auth = obtain_auth(args)
        bot = YouTubeMacro(auth)
        video_id, channel_id = bot.resolve_ids(args.video, args.channel)
        print(f"Resolved video={video_id} channel={channel_id}", flush=True)
        try:
            live_chat_id = bot.get_live_chat_id(video_id)
            print(f"liveChatId={live_chat_id}", flush=True)
        except BotError as exc:
            print(
                f"Data API liveChatId unavailable ({exc}); "
                "will use InnerTube send only",
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
                    f"[dry-run] would send to {video_id}: {args.command}",
                    flush=True,
                )
            else:
                via = bot.send_message(
                    channel_id=channel_id,
                    video_id=video_id,
                    live_chat_id=live_chat_id,
                    message=args.command,
                )
                print(f"Sent #{sent + 1} via {via}: {args.command}", flush=True)

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
