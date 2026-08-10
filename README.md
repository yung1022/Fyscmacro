# Fyscmacro (FYSC)

YouTube live chat macro bot that repeatedly sends a command/message into a livestream chat on an interval **you** choose. Triggered with a **GitHub Actions** workflow (`workflow_dispatch`).

> Use only where you are allowed to chat, and follow YouTube + channel rules. Short intervals can look like spam and may get you timed out, banned, or quota-limited.

## What you get

| Piece | Purpose |
| --- | --- |
| `bot/youtube_macro.py` | Resolves a live chat and sends your command on a timer |
| `.github/workflows/youtube-macro.yml` | Manual workflow: set video/channel, command, interval, duration |
| `tests/test_youtube_macro.py` | Offline unit tests |

## One-time Google OAuth setup

1. In [Google Cloud Console](https://console.cloud.google.com/) create (or pick) a project.
2. Enable **YouTube Data API v3**.
3. Create OAuth credentials: **Desktop app** (or Web) client → note **Client ID** and **Client secret**.
4. Get a **refresh token** with scope  
   `https://www.googleapis.com/auth/youtube.force-ssl`  
   Easy path: [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/)  
   - Gear icon → use your own OAuth credentials  
   - Authorize the YouTube Data API v3 scope above  
   - Exchange code → copy **Refresh token**
5. In this GitHub repo: **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
| --- | --- |
| `YOUTUBE_CLIENT_ID` | OAuth client ID |
| `YOUTUBE_CLIENT_SECRET` | OAuth client secret |
| `YOUTUBE_REFRESH_TOKEN` | Long-lived refresh token |

The Google account you authorize is the account that posts in chat.

## Run from GitHub Actions

1. Open **Actions** → **YouTube Live Chat Macro** → **Run workflow**.
2. Fill in:

| Input | Example | Meaning |
| --- | --- | --- |
| `video` | `https://www.youtube.com/watch?v=…` | Live video URL or 11-char ID (**preferred**) |
| `channel` | `@SomeCreator` | Used if `video` is empty; finds that channel’s current live |
| `command` | `!join` | Exact chat text to send |
| `interval_seconds` | `60` | Wait between sends (minimum `1`) |
| `duration_minutes` | `30` | How long to keep running (max `360`) |
| `send_count` | `0` | Optional cap; `0` = until duration ends |
| `dry_run` | `false` | `true` = log only, no YouTube calls |

3. Watch the job log for `Sent #N: ...`.

Only one macro run is active at a time (new runs cancel the previous).

## Run locally

```bash
export YOUTUBE_CLIENT_ID=...
export YOUTUBE_CLIENT_SECRET=...
export YOUTUBE_REFRESH_TOKEN=...

python3 bot/youtube_macro.py \
  --video 'https://www.youtube.com/watch?v=VIDEO_ID' \
  --command '!join' \
  --interval-seconds 60 \
  --duration-minutes 30
```

Or resolve the channel’s current livestream:

```bash
python3 bot/youtube_macro.py \
  --channel '@SomeCreator' \
  --command '!join' \
  --interval-seconds 60 \
  --duration-minutes 30
```

Dry run (no network):

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

- GitHub-hosted jobs time out around **6 hours**, so `duration_minutes` is capped at **360**.
- YouTube Data API has **quota** costs (especially `liveChatMessages.insert` and `search`). Prefer passing a **video URL/ID** over `channel` search.
- Keep intervals reasonable; chat moderation and API errors will stop the run if the stream ends or chat closes.
