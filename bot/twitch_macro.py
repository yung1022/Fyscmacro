#!/usr/bin/env python3
"""Twitch chat macro: send a message/command on a fixed interval."""

from __future__ import annotations

import argparse
import os
import socket
import ssl
import sys
import time
from typing import Optional


IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
RECV_SIZE = 4096


def normalize_oauth(token: str) -> str:
    token = token.strip()
    if not token:
        raise ValueError("OAuth token is empty")
    if token.lower().startswith("oauth:"):
        return token
    return f"oauth:{token}"


def normalize_channel(channel: str) -> str:
    channel = channel.strip().lstrip("#").lower()
    if not channel:
        raise ValueError("Channel is empty")
    return channel


def recv_until_ready(sock: ssl.SSLSocket, timeout: float = 15.0) -> None:
    """Drain welcome chatter and answer PINGs until JOIN is useful."""
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    buf = ""
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(RECV_SIZE)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk.decode("utf-8", errors="ignore")
        while "\r\n" in buf:
            line, buf = buf.split("\r\n", 1)
            if line.startswith("PING"):
                sock.sendall(f"PONG {line[5:]}\r\n".encode("utf-8"))
            # 001 = welcome; once we see it, connection is good enough
            if " 001 " in line:
                return


def drain_ping(sock: ssl.SSLSocket) -> None:
    """Non-blocking drain so Twitch PING does not drop the connection."""
    sock.settimeout(0.05)
    try:
        while True:
            chunk = sock.recv(RECV_SIZE)
            if not chunk:
                break
            for line in chunk.decode("utf-8", errors="ignore").split("\r\n"):
                if line.startswith("PING"):
                    sock.sendall(f"PONG {line[5:]}\r\n".encode("utf-8"))
    except (socket.timeout, BlockingIOError):
        pass
    finally:
        sock.settimeout(30.0)


def connect(nick: str, token: str, channel: str) -> ssl.SSLSocket:
    context = ssl.create_default_context()
    raw = socket.create_connection((IRC_HOST, IRC_PORT), timeout=30)
    sock = context.wrap_socket(raw, server_hostname=IRC_HOST)

    oauth = normalize_oauth(token)
    channel = normalize_channel(channel)
    nick = nick.strip().lstrip("@").lower()

    sock.sendall(f"PASS {oauth}\r\n".encode("utf-8"))
    sock.sendall(f"NICK {nick}\r\n".encode("utf-8"))
    sock.sendall(b"CAP REQ :twitch.tv/commands\r\n")
    recv_until_ready(sock)
    sock.sendall(f"JOIN #{channel}\r\n".encode("utf-8"))
    time.sleep(1.0)
    drain_ping(sock)
    return sock


def send_message(sock: ssl.SSLSocket, channel: str, message: str) -> None:
    channel = normalize_channel(channel)
    # Twitch PRIVMSG cannot contain newlines
    message = message.replace("\r", " ").replace("\n", " ").strip()
    if not message:
        raise ValueError("Message/command is empty")
    if len(message) > 500:
        raise ValueError("Message/command exceeds Twitch 500-character limit")
    sock.sendall(f"PRIVMSG #{channel} :{message}\r\n".encode("utf-8"))


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a Twitch chat command/message on a custom interval."
    )
    parser.add_argument(
        "--channel",
        default=os.getenv("TWITCH_CHANNEL", ""),
        help="Target Twitch channel (without #)",
    )
    parser.add_argument(
        "--command",
        default=os.getenv("COMMAND", ""),
        help="Chat message or command to send (e.g. !join)",
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
        "--username",
        default=os.getenv("TWITCH_USERNAME", ""),
        help="Your Twitch login name",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("TWITCH_OAUTH_TOKEN", ""),
        help="OAuth token (oauth:xxxx). Prefer env/secret.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.getenv("SEND_COUNT", "0")),
        help="Optional max number of sends (0 = until duration ends)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"},
        help="Log sends without connecting to Twitch",
    )
    return parser.parse_args(argv)


def validate(args: argparse.Namespace) -> None:
    if not args.channel:
        raise SystemExit("Missing --channel / TWITCH_CHANNEL")
    if not args.command:
        raise SystemExit("Missing --command / COMMAND")
    if args.interval_seconds < 1:
        raise SystemExit("interval-seconds must be >= 1 (avoid spam / rate limits)")
    if args.duration_minutes <= 0 and args.count <= 0:
        raise SystemExit("Set a positive duration-minutes and/or count")
    if not args.dry_run:
        if not args.username:
            raise SystemExit("Missing --username / TWITCH_USERNAME")
        if not args.token:
            raise SystemExit("Missing --token / TWITCH_OAUTH_TOKEN")


def run(args: argparse.Namespace) -> int:
    validate(args)
    channel = normalize_channel(args.channel)
    interval = args.interval_seconds
    duration_sec = max(0.0, args.duration_minutes) * 60.0
    end_at = time.monotonic() + duration_sec if duration_sec > 0 else None
    max_count = args.count if args.count > 0 else None

    print(
        f"Target=#{channel} interval={interval}s "
        f"duration={args.duration_minutes}m count={max_count or 'unlimited'} "
        f"dry_run={args.dry_run}",
        flush=True,
    )
    print(f"Command: {args.command!r}", flush=True)

    sock: Optional[ssl.SSLSocket] = None
    sent = 0
    try:
        if not args.dry_run:
            sock = connect(args.username, args.token, channel)
            print("Connected to Twitch IRC", flush=True)

        while True:
            if end_at is not None and time.monotonic() >= end_at:
                print("Duration reached; stopping", flush=True)
                break
            if max_count is not None and sent >= max_count:
                print("Send count reached; stopping", flush=True)
                break

            if args.dry_run:
                print(f"[dry-run] would send to #{channel}: {args.command}", flush=True)
            else:
                assert sock is not None
                drain_ping(sock)
                send_message(sock, channel, args.command)
                print(f"Sent #{sent + 1}: {args.command}", flush=True)

            sent += 1

            # Stop early if next wait would exceed duration and we have a count/duration bound
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
    finally:
        if sock is not None:
            try:
                sock.sendall(b"QUIT\r\n")
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    print(f"Done. Total sends: {sent}", flush=True)
    return 0


def main() -> None:
    args = parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
