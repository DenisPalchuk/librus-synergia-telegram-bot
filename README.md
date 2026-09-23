# Librus notifications in Telegram

A one-shot Python poller for Raspberry Pi. It sends new grades, announcements
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
2. Build the image: `docker compose build`.
3. Run it once: `docker compose run --rm -T bot`. The first run records the
   current state without sending old notifications.
4. Add this entry with `crontab -e` for a user who can access Docker (replace
   the project path):

   ```cron
   */15 * * * * cd /opt/librus-telegram-bot && /usr/bin/flock -n /tmp/librus-telegram-bot.lock /usr/bin/docker compose run --rm -T bot >> /opt/librus-telegram-bot/poll.log 2>&1
   ```

State is stored in the named Docker volume `librus_state` and survives container
recreation. Keep this volume when updating the project. To poll manually, run
`docker compose run --rm -T bot` again; later runs send only newly discovered
items. The command exits with status `1` on errors so they show up in the cron
log.

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
