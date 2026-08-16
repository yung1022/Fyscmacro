# Fyscmacro (FYSC)

YouTube live chat macro on a timer you choose, via **GitHub Actions** + **InnerTube**.

> YouTube blocked phone/TV **device OAuth** for live-chat send (and blocks that token from the public Data API). Sending now requires a logged-in **Cookie** header. You can get that from a phone — no desktop required.

## Setup (phone-friendly)

1. On Android, install **[Kiwi Browser](https://kiwibrowser.com/)** (has DevTools).
2. Open an **incognito** tab → sign into **YouTube**.
3. Kiwi menu → **Developer tools** → **Network**.
4. Load any `youtube.com` page → tap a request → **Request Headers** → copy **Cookie**.
5. Close the incognito tab.
6. GitHub repo → **Settings → Secrets and variables → Actions** → add:

| Secret | Value |
| --- | --- |
| `YOUTUBE_COOKIES` | Full Cookie string (must include `SAPISID` or `__Secure-3PAPISID`) |

The Google account behind those cookies is the account that posts in chat.

Cookies expire — if sends fail, copy a fresh Cookie string.

On API errors the bot **keeps retrying until `duration_minutes` wall-clock is used up** (retry waits count toward that duration).

## Run

**Actions → YouTube Live Chat Macro → Run workflow**

| Input | Example | Meaning |
| --- | --- | --- |
| `video` | live URL or `ImeRw_CxUio` | Preferred target |
| `channel` | `@SomeCreator` | Used if `video` empty |
| `command` | `!join` | Chat text |
| `interval_seconds` | `60` | Wait between sends |
| `duration_minutes` | `30` | Run length (max 360) |
| `send_count` | `0` | Optional cap |
| `dry_run` | `false` | Skip sends |

## Local

```bash
export YOUTUBE_COOKIES='SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...'

python3 bot/youtube_macro.py \
  --video 'https://www.youtube.com/watch?v=VIDEO_ID' \
  --command '!join' \
  --interval-seconds 60 \
  --duration-minutes 30
```

Dry run:

```bash
python3 bot/youtube_macro.py --video dQw4w9WgXcQ --command '!join' --count 3 --dry-run
```

## Tests

```bash
python3 -m unittest tests/test_youtube_macro.py -v
```

## Why not phone OAuth alone?

YouTube returns **403** on Data API and **400** on InnerTube `live_chat/send_message` for TV device-OAuth tokens. Cookie + `SAPISIDHASH` is what the web client uses to post chat.
