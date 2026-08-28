# All-in-One Video Downloader + Channel Post Bot

## Features
- YouTube, Facebook, Instagram, TikTok, Pinterest, Twitter/X, Reddit, Threads, Vimeo, Dailymotion, Twitch, Rumble, LinkedIn, Bilibili, SoundCloud, Streamable
- Download video → show with **Edit** + **Post** buttons
- Edit caption
- Post to your Telegram Channel
- All users can use
- Admin can view all users with `/users`

## Setup

1. Create bot with @BotFather → get BOT_TOKEN
2. Get API_ID + API_HASH from https://my.telegram.org
3. Copy `.env.example` to `.env` and fill values
4. Install dependencies:

```bash
pip install -r requirements.txt
```

5. Run:

```bash
python bot.py
```

## Usage

1. `/start`
2. `/setchannel` → enter your channel username (e.g. mychannel)
3. Make the bot **Admin** of that channel (allow Post Messages)
4. Send any supported video link
5. When video appears:
   - ✏️ **Edit** → change caption
   - 📤 **Post** → send to your channel

Admin only:
- `/users` → list all users who used the bot

## Notes
- Large videos may hit Telegram 50MB limit (normal Bot API)
- For YouTube age-restricted / private, put cookies.txt
- Bot must be Admin in the target channel
