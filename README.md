# Librus notifications in Telegram

A background container for Raspberry Pi. Its internal cron scheduler runs a
one-shot Python poller every 15 minutes and sends new grades, announcements
(`Ogłoszenia`), and incoming messages (`Wiadomości`) to a Telegram chat. It uses
the Librus reading approach from [LibrusSynergiaHA](https://github.com/LukMaverick/LibrusSynergiaHA)
and keeps notification history in SQLite across runs.

The bot does not open incoming messages. It sends their subject, sender, date,
and attachment indicator, so messages are not marked as read in Librus.

## Raspberry Pi setup

Docker and the Docker Compose plugin are required. A 64-bit Raspberry Pi OS is
recommended; Docker will pull the ARM64 variant of `python:3.12-slim`.

1. Copy `.env.example` to `.env` and fill in the Librus username/password,
   Telegram bot token, and chat ID. The bot must be a member of the chat and
   allowed to post. `.env` is ignored by Git; restrict its permissions on the
   Pi with `chmod 600 .env`. Single-quote values containing `$` or `#`.
2. Start the container: `docker compose up -d --build`.
3. Follow its output with `docker compose logs -f bot`. The first scheduled
   poll runs at the next quarter-hour mark. It records the current state
   without sending old notifications; later polls send new items.

State is stored in the named Docker volume `librus_state` and survives container
recreation. Keep this volume when updating the project. The scheduler runs in
the foreground as the container's main process, and Compose restarts the
container unless it was stopped manually. No host crontab is needed. To change
the interval, edit [`crontab`](crontab) and run `docker compose up -d --build`.
To trigger a one-off poll in the running container, use
`docker compose exec bot python /app/bot.py`. Failed polls appear in
`docker compose logs bot` and are retried on the next schedule.

`TELEGRAM_CHAT_ID` can be negative for a group. To find it, add the bot to the
chat, send a message, and inspect `getUpdates` in the Telegram Bot API. Keep the
bot token out of public logs.

## Failure behavior

The script reads all Librus sources before recording new items as pending. A
Librus read failure does not change the state. Each item is marked as sent only
after Telegram confirms it; failed sends remain pending for the next run. A
process interruption between Telegram's response and the SQLite update can
cause one duplicate message because `sendMessage` has no idempotency key.

An edited announcement is treated as new: Librus provides no separate ID for
announcements, so the bot fingerprints its title, author, date, and text.
Incoming messages are read page by page until a previously seen message is
found. The initial run scans all pages.

Run the tests locally without account credentials after installing dependencies:
`python3 -m venv .venv`, `.venv/bin/pip install -r requirements.txt`, then
`.venv/bin/python -m unittest -v`.
