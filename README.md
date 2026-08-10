# Fyscmacro (FYSC)

YouTube live chat macro that posts a command on a timer **you** set, using YouTube’s **InnerTube** API (`/youtubei/v1/live_chat/send_message`). Triggered with a **GitHub Actions** workflow.

> Use only where you are allowed to chat. Short intervals look like spam and can get you timed out or banned. InnerTube is unofficial; cookies expire and YouTube can change endpoints.

## What you get

| Piece | Purpose |
| --- | --- |
| `bot/youtube_macro.py` | InnerTube client + interval sender |
| `.github/workflows/youtube-macro.yml` | Manual workflow inputs |
| `tests/test_youtube_macro.py` | Offline unit tests |

## One-time cookie setup

InnerTube chat send needs a **logged-in browser session**, not official OAuth client secrets.

1. Open an **incognito/private** window and sign into YouTube (incognito avoids cookie rotation while you copy).
2. Open DevTools → **Network**.
3. Load any `youtube.com` page and click a request to `www.youtube.com`.
4. Copy the full **`Cookie`** request header.
5. Close the incognito window.
6. In this repo: **Settings → Secrets and variables → Actions** → add:

| Secret | Value |
| --- | --- |
| `YOUTUBE_COOKIES` | Full Cookie header string |

It must include **`SAPISID`** (or `__Secure-3PAPISID`). Also keep `SID` / `HSID` / `SSID` / `APISID` / `__Secure-1PSID` if present.

The Google account behind those cookies is the account that posts in chat.

## Run from GitHub Actions

1. **Actions** → **YouTube Live Chat Macro** → **Run workflow**
2. Inputs:

| Input | Example | Meaning |
| --- | --- | --- |
| `video` | `https://www.youtube.com/watch?v=…` | Live video URL or ID (**preferred**) |
| `channel` | `@SomeCreator` | If `video` empty, opens `/@handle/live` |
| `command` | `!join` | Exact chat text |
| `interval_seconds` | `60` | Wait between sends (min `1`) |
| `duration_minutes` | `30` | Run length (max `360`) |
| `send_count` | `0` | Optional cap; `0` = until duration ends |
| `dry_run` | `false` | `true` = no YouTube calls |

How it works:

1. Resolve `videoId` + `channelId` from the watch page / `/live`
2. Build InnerTube `params` (protobuf)
3. `POST /youtubei/v1/live_chat/send_message` with `SAPISIDHASH` auth
4. Sleep `interval_seconds`, repeat until duration/count ends

## Run locally

```bash
export YOUTUBE_COOKIES='SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...'

python3 bot/youtube_macro.py \
  --video 'https://www.youtube.com/watch?v=VIDEO_ID' \
  --command '!join' \
  --interval-seconds 60 \
  --duration-minutes 30
```

Or by channel live page:

```bash
python3 bot/youtube_macro.py \
  --channel '@SomeCreator' \
  --command '!join' \
  --interval-seconds 60 \
  --duration-minutes 30
```

Dry run:

```bash
python3 bot/youtube_macro.py \
  --video dQw4w9WgXcQ \
  --command '!join' \
  --interval-seconds 1 \
  --count 3 \
  --dry-run
```

## Tests

```bash
python3 -m unittest tests/test_youtube_macro.py -v
```

## Limits

- Job timeout ~**6 hours** (`duration_minutes` ≤ **360**).
- Cookies go stale; if sends fail, refresh `YOUTUBE_COOKIES`.
- Prefer a **video URL** over channel auto-detect when the stream is already open.
