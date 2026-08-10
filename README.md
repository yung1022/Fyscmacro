# Fyscmacro (FYSC)

Twitch chat macro bot that repeatedly sends a command/message into a livestream chat on an interval **you** choose. Triggered with a **GitHub Actions** workflow (`workflow_dispatch`).

> Use only on channels where you are allowed to chat, and follow Twitch + channel rules. Short intervals can look like spam and may get you timed out or banned.

## What you get

| Piece | Purpose |
| --- | --- |
| `bot/twitch_macro.py` | Connects to Twitch IRC and sends your command on a timer |
| `.github/workflows/twitch-macro.yml` | Manual workflow: set channel, command, interval, duration |
| `tests/test_twitch_macro.py` | Offline unit tests |

## One-time setup

1. Create a Twitch account the bot will chat as (or use your own).
2. Generate a chat OAuth token with **`chat:send`** (and usually `chat:read`), e.g. via [Twitch Token Generator](https://twitchtokengenerator.com/) or the [Twitch developer console](https://dev.twitch.tv/).
3. In this GitHub repo go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
| --- | --- |
| `TWITCH_USERNAME` | Your Twitch login (lowercase is fine) |
| `TWITCH_OAUTH_TOKEN` | Token like `oauth:xxxxxxxx` or just the token body |

## Run from GitHub Actions

1. Open the **Actions** tab → **Twitch Chat Macro** → **Run workflow**.
2. Fill in the inputs:

| Input | Example | Meaning |
| --- | --- | --- |
| `channel` | `some_streamer` | Livestream channel (no `#`) |
| `command` | `!join` | Exact chat text to send |
| `interval_seconds` | `60` | Wait between sends (minimum `1`) |
| `duration_minutes` | `30` | How long to keep running (max `360`) |
| `send_count` | `0` | Optional cap; `0` = until duration ends |
| `dry_run` | `false` | `true` = log only, no Twitch connection |

3. Watch the job log for `Sent #N: ...`.

Only one macro run is active at a time (new runs cancel the previous).

## Run locally

```bash
export TWITCH_USERNAME=your_name
export TWITCH_OAUTH_TOKEN=oauth:your_token

python bot/twitch_macro.py \
  --channel some_streamer \
  --command '!join' \
  --interval-seconds 60 \
  --duration-minutes 30
```

Dry run (no network):

```bash
python bot/twitch_macro.py \
  --channel some_streamer \
  --command '!join' \
  --interval-seconds 1 \
  --count 3 \
  --dry-run
```

## Tests

```bash
python -m unittest tests/test_twitch_macro.py -v
```

## Limits

- GitHub-hosted jobs time out around **6 hours**, so `duration_minutes` is capped at **360**.
- Twitch rate-limits chat; keep intervals reasonable (often **30–60s+**).
- Tokens expire; if sends fail after auth errors, regenerate `TWITCH_OAUTH_TOKEN`.
