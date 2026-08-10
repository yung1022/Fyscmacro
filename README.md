# Fyscmacro (FYSC)

YouTube live chat macro on a timer you choose, via **GitHub Actions**.

Uses **both**:
- **InnerTube** — find the live video / channel (and InnerTube send fallback)
- **YouTube Data API** — `liveChatMessages.insert` when the OAuth token allows it

## No desktop. No required secrets.

Auth is **YouTube TV device login** on your **phone**:

1. Start **Actions → YouTube Live Chat Macro → Run workflow**
2. Open the running job log
3. When you see a code, open the printed URL on your phone (usually `https://www.google.com/device`)
4. Enter the code and approve
5. The job continues and sends your command on the interval

You do **not** need to copy browser cookies or create a Google Cloud project for the default path.

## Workflow inputs

| Input | Example | Meaning |
| --- | --- | --- |
| `video` | live URL or ID | Preferred target |
| `channel` | `@SomeCreator` | Uses `/live` if `video` empty |
| `command` | `!join` | Chat text to send |
| `interval_seconds` | `60` | Wait between sends |
| `duration_minutes` | `30` | How long to run (max 360) |
| `send_count` | `0` | Optional cap |
| `auth_timeout_seconds` | `600` | How long to wait for phone login |
| `dry_run` | `false` | Skip login/sends |

## Optional: skip phone login next time

After a successful login the log may print a **refresh token**. If you want unattended runs, add these repo secrets (optional):

| Secret | When |
| --- | --- |
| `YOUTUBE_REFRESH_TOKEN` | From a previous successful login log |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` | Only if you override the auto TV client |

If those secrets are missing, each run just asks you to approve on your phone again.

## Local run

```bash
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

## Notes

- Follow YouTube + channel chat rules; short intervals can get you timed out.
- Device codes expire (~30 minutes); raise `auth_timeout_seconds` if you need longer.
- Prefer a **video URL** when the stream is already open.
