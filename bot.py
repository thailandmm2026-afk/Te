# ============================================================
# All-in-One Video Downloader + Channel Post Bot
# Supports: YouTube, Facebook, Instagram, TikTok, Pinterest,
# Twitter/X, Reddit, Threads, Vimeo, Dailymotion, Twitch,
# Rumble, LinkedIn, Bilibili, SoundCloud, Streamable
# + Edit Caption + Post to Channel
# Based on original All-in-One | Modified for Movie/Channel use
# ============================================================

import os
import re
import io
import json
import time
import random
import asyncio
import tempfile
import subprocess
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ForceReply,
)
from pyrogram.errors import FloodWait, ChatAdminRequired, ChannelInvalid, ChannelPrivate

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from pinterest_downloader import Pinterest
    pinterest = Pinterest()
except Exception:
    pinterest = None

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

if not BOT_TOKEN or not API_ID or not API_HASH:
    raise ValueError("API_ID, API_HASH and BOT_TOKEN environment variables are required")

ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

OUTPUT_FOLDER = "downloads"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

CREDIT = "@KMM_MOD1"
COOKIES_FILE = "cookies.txt"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
})

app = Client(
    "all_in_one_post_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

_executor = ThreadPoolExecutor(max_workers=4)

# ──────────────────────────────────────────────
# Data storage
# ──────────────────────────────────────────────
USERS_FILE = "users.json"
CHANNELS_FILE = "user_channels.json"
PENDING = {}  # user_id -> dict with file_id, caption, title, message_id

def load_json(path, default=None):
    if default is None:
        default = {}
    try:
        if Path(path).exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

users_db = load_json(USERS_FILE, {})
channels_db = load_json(CHANNELS_FILE, {})

def track_user(user):
    if not user:
        return
    uid = str(user.id)
    users_db[uid] = {
        "id": user.id,
        "first_name": user.first_name or "",
        "username": user.username or "",
        "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_json(USERS_FILE, users_db)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def make_buttons():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit", callback_data="edit_caption"),
            InlineKeyboardButton("📤 Post", callback_data="post_video"),
        ]
    ])

# ──────────────────────────────────────────────
# Shared utilities
# ──────────────────────────────────────────────
def human_size(num: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if num < 1024:
            return f"{num:.2f} {unit}"
        num /= 1024
    return f"{num:.2f} TB"


def progress_bar(current: int, total: int, width: int = 10) -> str:
    if total == 0:
        return "░" * width
    filled = int(width * current / total)
    return f"[{'█' * filled}{'░' * (width - filled)}] {current / total * 100:.1f}%"


_last_edit: dict[int, float] = {}


async def safe_edit(msg: Message, text: str, min_interval: float = 1.8) -> None:
    now = time.time()
    if now - _last_edit.get(msg.id, 0) < min_interval:
        return
    _last_edit[msg.id] = now
    try:
        await msg.edit_text(text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception:
        pass


def make_upload_cb(status_msg: Message, loop: asyncio.AbstractEventLoop):
    last = [0.0]

    def cb(current: int, total: int):
        now = time.time()
        if now - last[0] < 2.0:
            return
        last[0] = now
        bar = progress_bar(current, total)
        text = (
            f"📤 **Telegram သို့ ပေးပို့နေပါသည်...**\n"
            f"{bar}\n"
            f"`{human_size(current)} / {human_size(total)}`\n\n"
            f"— {CREDIT}"
        )
        asyncio.run_coroutine_threadsafe(
            safe_edit(status_msg, text, min_interval=0), loop
        )

    return cb


def extract_thumbnail(video_path: str, thumb_path: str) -> bool:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(probe.stdout.strip() or "10")
        seek = random.uniform(duration * 0.10, duration * 0.80)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-ss", str(seek), "-i", video_path,
             "-vframes", "1", "-vf", "scale=320:-1", "-y", thumb_path],
            timeout=30, check=True,
        )
        return os.path.exists(thumb_path)
    except Exception:
        return False


def get_video_metadata(video_path: str) -> tuple[int, int, int]:
    try:
        dur = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = int(float(dur.stdout.strip() or "0"))
    except Exception:
        duration = 0
    try:
        dim = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=30,
        )
        w, h = dim.stdout.strip().split(",")
        width, height = int(w), int(h)
    except Exception:
        width, height = 1280, 720
    return duration, width, height


# ──────────────────────────────────────────────
# URL detectors
# ──────────────────────────────────────────────
FB_PATTERN = re.compile(r"(https?://)?(www\.)?(facebook\.com|fb\.watch|fb\.com)/\S+", re.I)
YT_PATTERN = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+", re.I)
INSTAGRAM_PATTERN = re.compile(r"(https?://)?(www\.)?instagram\.com/\S+", re.I)
TWITTER_PATTERN = re.compile(r"(https?://)?(www\.)?(twitter\.com|x\.com)/\S+", re.I)
PINTEREST_PATTERN = re.compile(r"(https?://)?(www\.)?(pinterest\.com|pin\.it)/\S+", re.I)
TIKTOK_PATTERN = re.compile(r"(https?://)?(www\.)?(tiktok\.com|vm\.tiktok\.com)/\S+", re.I)
REDDIT_PATTERN = re.compile(r"(https?://)?(www\.)?(reddit\.com|redd\.it)/\S+", re.I)
THREADS_PATTERN = re.compile(r"(https?://)?(www\.)?threads\.net/\S+", re.I)
VIMEO_PATTERN = re.compile(r"(https?://)?(www\.)?vimeo\.com/\S+", re.I)
DAILYMOTION_PATTERN = re.compile(r"(https?://)?(www\.)?dailymotion\.com/\S+", re.I)
TWITCH_PATTERN = re.compile(r"(https?://)?(www\.)?twitch\.tv/\S+", re.I)
RUMBLE_PATTERN = re.compile(r"(https?://)?(www\.)?rumble\.com/\S+", re.I)
LINKEDIN_PATTERN = re.compile(r"(https?://)?(www\.)?linkedin\.com/\S+", re.I)
BILIBILI_PATTERN = re.compile(r"(https?://)?(www\.)?(bilibili\.com|b23\.tv)/\S+", re.I)
SOUNDCLOUD_PATTERN = re.compile(r"(https?://)?(www\.)?soundcloud\.com/\S+", re.I)
STREAMABLE_PATTERN = re.compile(r"(https?://)?(www\.)?streamable\.com/\S+", re.I)


def detect_platform(text: str) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    patterns = [
        (FB_PATTERN, "facebook"),
        (YT_PATTERN, "youtube"),
        (INSTAGRAM_PATTERN, "instagram"),
        (TWITTER_PATTERN, "twitter"),
        (PINTEREST_PATTERN, "pinterest"),
        (TIKTOK_PATTERN, "tiktok"),
        (REDDIT_PATTERN, "reddit"),
        (THREADS_PATTERN, "threads"),
        (VIMEO_PATTERN, "vimeo"),
        (DAILYMOTION_PATTERN, "dailymotion"),
        (TWITCH_PATTERN, "twitch"),
        (RUMBLE_PATTERN, "rumble"),
        (LINKEDIN_PATTERN, "linkedin"),
        (BILIBILI_PATTERN, "bilibili"),
        (SOUNDCLOUD_PATTERN, "soundcloud"),
        (STREAMABLE_PATTERN, "streamable"),
    ]
    for pattern, name in patterns:
        m = pattern.search(text)
        if m:
            return name, m.group(0)
    return None, None


# ──────────────────────────────────────────────
# yt-dlp base helpers
# ──────────────────────────────────────────────
def get_base_ydl_opts(use_cookies: bool = True) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 8,
        "buffersize": 1024 * 1024,
        "http_chunk_size": 10 * 1024 * 1024,
        "format_sort": ["res", "ext:mp4:m4a", "codec:h264:aac", "size"],
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if shutil.which("node"):
        opts["js_runtimes"] = {"node": None}
        opts["remote_components"] = ["ejs:github"]

    env_cookies = os.environ.get("USE_COOKIES", "1").strip().lower() not in ("0", "false", "no")
    if use_cookies and env_cookies and os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 100:
        opts["cookiefile"] = COOKIES_FILE
    return opts


def _run_ydl(ydl_opts: dict, url: str):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)


def _download_yt_sync(ydl_opts: dict, url: str):
    try:
        info = _run_ydl(ydl_opts, url)
        # Find the downloaded file
        if "requested_downloads" in info and info["requested_downloads"]:
            path = info["requested_downloads"][0].get("filepath")
        else:
            path = ydl_opts.get("outtmpl")
            if isinstance(path, dict):
                path = path.get("default", path)
            # Try to resolve
            if path and "%(" in str(path):
                path = None
        return info, path
    except Exception as e:
        return None, str(e)


def make_progress_hook(status_msg, loop, prefix="Downloading"):
    last = [0.0]
    def hook(d):
        if d["status"] == "downloading":
            now = time.time()
            if now - last[0] < 1.5:
                return
            last[0] = now
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            current = d.get("downloaded_bytes", 0)
            if total > 0:
                bar = progress_bar(current, total)
                text = f"📥 **{prefix}**\n{bar}\n`{human_size(current)} / {human_size(total)}`\n\n— {CREDIT}"
                asyncio.run_coroutine_threadsafe(safe_edit(status_msg, text, min_interval=0), loop)
    return hook


# ──────────────────────────────────────────────
# Core: Download then send with Edit/Post buttons
# ──────────────────────────────────────────────
async def send_with_buttons(client: Client, message: Message, file_path: str, title: str, status: Message = None):
    """Send video/audio/photo with Edit + Post buttons and store in PENDING."""
    user_id = message.from_user.id
    caption = f"🎬 {title[:200]}"

    ext = file_path.lower().split(".")[-1] if file_path else ""

    try:
        if ext in ("mp3", "m4a", "ogg", "opus", "flac", "wav"):
            sent = await client.send_audio(
                chat_id=message.chat.id,
                audio=file_path,
                caption=caption,
                title=title[:60],
                reply_markup=make_buttons(),
            )
            file_id = sent.audio.file_id
        elif ext in ("mp4", "mkv", "webm", "mov", "avi"):
            duration, width, height = await asyncio.to_thread(get_video_metadata, file_path)
            thumb_path = file_path + "_thumb.jpg"
            has_thumb = await asyncio.to_thread(extract_thumbnail, file_path, thumb_path)
            sent = await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                caption=caption,
                duration=duration,
                width=width,
                height=height,
                thumb=thumb_path if has_thumb else None,
                supports_streaming=True,
                reply_markup=make_buttons(),
            )
            file_id = sent.video.file_id
            if has_thumb and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass
        elif ext == "gif":
            sent = await client.send_animation(
                chat_id=message.chat.id,
                animation=file_path,
                caption=caption,
                reply_markup=make_buttons(),
            )
            file_id = sent.animation.file_id
        else:
            sent = await client.send_photo(
                chat_id=message.chat.id,
                photo=file_path,
                caption=caption,
                reply_markup=make_buttons(),
            )
            file_id = sent.photo.file_id

        PENDING[user_id] = {
            "file_id": file_id,
            "caption": caption,
            "title": title,
            "message_id": sent.id,
            "is_video": ext in ("mp4", "mkv", "webm", "mov", "avi"),
            "is_audio": ext in ("mp3", "m4a", "ogg", "opus", "flac", "wav"),
        }

        if status:
            try:
                await status.delete()
            except Exception:
                pass

    except Exception as e:
        if status:
            await safe_edit(status, f"❌ ပို့လို့ မရပါ:\n`{str(e)[:250]}`\n\n— {CREDIT}")
        else:
            await message.reply_text(f"❌ ပို့လို့ မရပါ:\n`{str(e)[:250]}`")


async def handle_ytdlp(client: Client, message: Message, url: str, platform_name: str) -> None:
    if not yt_dlp:
        await message.reply_text("yt-dlp မရှိပါ။")
        return

    track_user(message.from_user)
    loop = asyncio.get_event_loop()
    status = await message.reply_text(
        f"🔍 **{platform_name} link detected!**\nStarting...\n\n— {CREDIT}"
    )

    safe_title = re.sub(r'[\\/*?:"<>|]', "", platform_name)[:40] + "_" + str(int(time.time()))
    outtmpl = os.path.join(OUTPUT_FOLDER, f"{safe_title}.%(ext)s")

    ydl_opts = get_base_ydl_opts()
    ydl_opts["outtmpl"] = outtmpl
    ydl_opts["progress_hooks"] = [make_progress_hook(status, loop, f"{platform_name} Downloading...")]

    if platform_name.lower() == "soundcloud":
        ydl_opts["format"] = "bestaudio/best"

    try:
        await safe_edit(status, f"📥 **{platform_name} ကို Download လုပ်နေပါသည်...**\n\n— {CREDIT}")
        info, path = await asyncio.wait_for(
            loop.run_in_executor(None, _download_yt_sync, ydl_opts, url),
            timeout=600,
        )
        if not info:
            await safe_edit(status, f"❌ Media အချက်အလက် မရရှိပါ။\n\n— {CREDIT}")
            return

        final_path = path or ""
        if not os.path.exists(final_path):
            for f in os.listdir(OUTPUT_FOLDER):
                if f.startswith(safe_title):
                    final_path = os.path.join(OUTPUT_FOLDER, f)
                    break

        if not os.path.exists(final_path):
            await safe_edit(status, f"❌ ဖိုင် မတွေ့ပါ။\n\n— {CREDIT}")
            return

        title = info.get("title") or info.get("description") or f"{platform_name} Media"
        await safe_edit(status, f"📤 Telegram သို့ ပို့နေပါသည်...\n\n— {CREDIT}")

        await send_with_buttons(client, message, final_path, title, status)

    except asyncio.TimeoutError:
        await safe_edit(status, f"❌ ဒေါင်းလုဒ် အချိန်ကျော်လွန်သွားပါပြီ။\n\n— {CREDIT}")
    except Exception as e:
        await safe_edit(status, f"❌ အမှား:\n`{str(e)[:300]}`\n\n— {CREDIT}")
    finally:
        for f in os.listdir(OUTPUT_FOLDER):
            if f.startswith(safe_title):
                try:
                    os.remove(os.path.join(OUTPUT_FOLDER, f))
                except Exception:
                    pass


# ──────────────────────────────────────────────
# Facebook special (simplified to yt-dlp fallback + direct if possible)
# ──────────────────────────────────────────────
async def handle_facebook(client: Client, message: Message, url: str) -> None:
    # Prefer yt-dlp for Facebook as it is more reliable now
    await handle_ytdlp(client, message, url, "Facebook")


# ──────────────────────────────────────────────
# Pinterest
# ──────────────────────────────────────────────
async def handle_pinterest(client: Client, message: Message, url: str) -> None:
    track_user(message.from_user)
    status = await message.reply_text(f"📌 Pinterest link detected!\n\n— {CREDIT}")

    if not pinterest:
        # Fallback to yt-dlp
        await handle_ytdlp(client, message, url, "Pinterest")
        return

    try:
        await safe_edit(status, "📥 Pinterest ကို Download လုပ်နေပါသည်...\n\n— {CREDIT}")
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                dl = await asyncio.to_thread(pinterest.download_pin, url, path=tmpdir)
            except Exception:
                dl = None

            if not dl:
                await handle_ytdlp(client, message, url, "Pinterest")
                return

            # Find downloaded file
            files = list(Path(tmpdir).rglob("*"))
            media_files = [f for f in files if f.is_file() and f.suffix.lower() in (".mp4", ".jpg", ".jpeg", ".png", ".gif", ".webp")]
            if not media_files:
                await safe_edit(status, "❌ Media မတွေ့ပါ။\n\n— {CREDIT}")
                return

            file_path = str(media_files[0])
            title = "Pinterest Media"
            await send_with_buttons(client, message, file_path, title, status)

    except Exception as e:
        await safe_edit(status, f"❌ အမှား:\n`{str(e)[:250]}`\n\n— {CREDIT}")


# ──────────────────────────────────────────────
# TikTok (simplified via yt-dlp which works well)
# ──────────────────────────────────────────────
async def handle_tiktok(client: Client, message: Message, url: str) -> None:
    await handle_ytdlp(client, message, url, "TikTok")


# ──────────────────────────────────────────────
# Router + State handlers (Caption edit + Channel set + Link routing)
# ──────────────────────────────────────────────
WAITING_CAPTION = set()   # user_ids waiting for new caption
WAITING_CHANNEL = set()   # user_ids waiting for channel

@app.on_callback_query(filters.regex("^edit_caption$"))
async def edit_caption_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if user_id not in PENDING:
        await query.answer("Video မရှိတော့ပါ။ Link အသစ်ပို့ပါ။", show_alert=True)
        return

    await query.answer()
    WAITING_CAPTION.add(user_id)
    await query.message.reply_text(
        "✏️ **ပြင်ချင်တဲ့ Caption (စာသား) အသစ်ကို ရိုက်ထည့်ပါ။**\n\n"
        "ပယ်ဖျက်ချင်ရင် `/cancel` ရိုက်ပါ။"
    )


@app.on_message(filters.private & filters.text & ~filters.command(["start", "help", "setchannel", "users", "mychannel"]))
async def handle_text_states(client: Client, message: Message):
    """Handle caption edit + channel set + normal link routing"""
    user_id = message.from_user.id
    track_user(message.from_user)
    text = (message.text or "").strip()

    # ─── Waiting for new caption ───
    if user_id in WAITING_CAPTION:
        WAITING_CAPTION.discard(user_id)

        if text.lower() == "/cancel":
            await message.reply_text("ပယ်ဖျက်လိုက်ပါပြီ။")
            return

        if user_id not in PENDING:
            await message.reply_text("Video မရှိတော့ပါ။ Link အသစ်ပို့ပါ။")
            return

        new_caption = text
        PENDING[user_id]["caption"] = new_caption

        try:
            await client.edit_message_caption(
                chat_id=message.chat.id,
                message_id=PENDING[user_id]["message_id"],
                caption=new_caption,
                reply_markup=make_buttons()
            )
            await message.reply_text("✅ **Caption ပြင်ပြီးပါပြီ။**")
        except Exception as e:
            await message.reply_text(f"❌ ပြင်လို့ မရပါ:\n`{str(e)[:200]}`")
        return

    # ─── Waiting for channel ───
    if user_id in WAITING_CHANNEL:
        WAITING_CHANNEL.discard(user_id)

        if text.lower() == "/cancel":
            await message.reply_text("ပယ်ဖျက်လိုက်ပါပြီ။")
            return

        # Accept both @username and numeric ID (-100...)
        raw = text.strip()
        if raw.startswith("@"):
            channel = raw[1:].strip()
        elif raw.lstrip("-").isdigit():
            channel = raw  # numeric ID like -1001234567890
        else:
            channel = raw.lstrip("@").strip()

        if not channel:
            await message.reply_text("မှန်ကန်တဲ့ Channel username သို့မဟုတ် ID ထည့်ပေးပါ။")
            return

        channels_db[str(user_id)] = channel
        save_json(CHANNELS_FILE, channels_db)

        if str(channel).startswith("-100"):
            display = f"`{channel}` (Private Channel ID)"
            hint = (
                "1. Channel ထဲ ဝင်ပါ\n"
                "2. ဒီ Bot ကို **Admin** အဖြစ် ထည့်ပါ\n"
                "3. **Post Messages** ခွင့်ပြုပေးပါ"
            )
        else:
            display = f"**@{channel}**"
            hint = (
                f"1. @{channel} Channel ထဲ ဝင်ပါ\n"
                "2. ဒီ Bot ကို **Admin** အဖြစ် ထည့်ပါ\n"
                "3. **Post Messages** ခွင့်ပြုပေးပါ"
            )

        await message.reply_text(
            f"✅ Channel သတ်မှတ်ပြီးပါပြီ → {display}\n\n"
            f"⚠️ **အရေးကြီး**\n{hint}\n\n"
            "ပြီးရင် Video Link ပို့ → **Post** ခလုတ် နှိပ်နိုင်ပါပြီ။"
        )
        return

    # ─── Normal link routing ───
    platform, url = detect_platform(text)
    if not platform or not url:
        await message.reply_text(
            "Link မတွေ့ပါ။\n\n"
            "Supported: YouTube, Facebook, Instagram, TikTok, Pinterest, Twitter/X, Reddit, Threads, Vimeo ...\n\n"
            "Link တစ်ခု ပို့ပေးပါ။\n\n"
            "Channel သတ်မှတ်ချင်ရင် → /setchannel"
        )
        return

    if platform == "facebook":
        await handle_facebook(client, message, url)
    elif platform == "pinterest":
        await handle_pinterest(client, message, url)
    elif platform == "tiktok":
        await handle_tiktok(client, message, url)
    else:
        name_map = {
            "youtube": "YouTube",
            "instagram": "Instagram",
            "twitter": "Twitter/X",
            "reddit": "Reddit",
            "threads": "Threads",
            "vimeo": "Vimeo",
            "dailymotion": "Dailymotion",
            "twitch": "Twitch",
            "rumble": "Rumble",
            "linkedin": "LinkedIn",
            "bilibili": "Bilibili",
            "soundcloud": "SoundCloud",
            "streamable": "Streamable",
        }
        await handle_ytdlp(client, message, url, name_map.get(platform, platform.title()))


# ──────────────────────────────────────────────
# Post to Channel
# ──────────────────────────────────────────────
def get_chat_id(channel: str):
    """Return proper chat_id for send methods"""
    if str(channel).startswith("-100") or str(channel).lstrip("-").isdigit():
        return int(channel)
    return f"@{channel}"


@app.on_callback_query(filters.regex("^post_video$"))
async def post_video_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if user_id not in PENDING:
        await query.answer("Video မရှိတော့ပါ။ Link အသစ်ပို့ပါ။", show_alert=True)
        return

    channel = channels_db.get(str(user_id))
    if not channel:
        await query.answer()
        await query.message.reply_text(
            "သင့် Channel ကို အရင် သတ်မှတ်ပါ။\n\n"
            "/setchannel ရိုက်ပြီး Channel username သို့မဟုတ် ID ထည့်ပေးပါ။"
        )
        return

    await query.answer("Channel ပေါ် တင်နေပါတယ်...")

    data = PENDING[user_id]
    chat_id = get_chat_id(channel)

    try:
        if data.get("is_audio"):
            await client.send_audio(
                chat_id=chat_id,
                audio=data["file_id"],
                caption=data["caption"],
            )
        elif data.get("is_video"):
            await client.send_video(
                chat_id=chat_id,
                video=data["file_id"],
                caption=data["caption"],
                supports_streaming=True,
            )
        else:
            await client.send_photo(
                chat_id=chat_id,
                photo=data["file_id"],
                caption=data["caption"],
            )

        display = f"`{channel}`" if str(channel).startswith("-") else f"@{channel}"
        await query.message.reply_text(
            f"✅ **Channel ပေါ် တင်ပြီးပါပြီ!**\n→ {display}\n\n"
            f"🎬 {data.get('title', '')[:80]}"
        )
    except ChatAdminRequired:
        await query.message.reply_text(
            "❌ Bot ကို Channel မှာ **Admin** မပေးရသေးပါ။\n\n"
            "**လုပ်ရမယ့်အဆင့်များ:**\n"
            "1. Channel ထဲ ဝင်ပါ\n"
            "2. Bot ကို Admin အဖြစ် ထည့်ပါ\n"
            "3. **Post Messages** ခွင့်ပြုပါ\n\n"
            "ပြီးရင် ဒီ **Post** ခလုတ်ကို ပြန်နှိပ်ပါ။"
        )
    except (ChannelInvalid, ChannelPrivate) as e:
        await query.message.reply_text(
            "❌ Channel ကို ရှာမတွေ့ပါ သို့မဟုတ် Private ဖြစ်နေပါတယ်။\n\n"
            "• Public Channel ဆိုရင် `@username` ထည့်ပါ\n"
            "• Private Channel ဆိုရင် Channel ID (`-100...`) ထည့်ပါ\n\n"
            "/setchannel နဲ့ ပြန်သတ်မှတ်ပါ။"
        )
    except Exception as e:
        err = str(e)
        if "USERNAME_INVALID" in err:
            await query.message.reply_text(
                "❌ **Username မမှန်ပါ**\n\n"
                "• Public Channel ဆိုရင် မှန်ကန်တဲ့ `@username` ထည့်ပါ\n"
                "• Private Channel ဆိုရင် **Channel ID** (`-100xxxxxxxxxx`) ထည့်ပါ\n\n"
                "**Channel ID ရယူနည်း:**\n"
                "1. @userinfobot သို့မဟုတ် @getidsbot ကို Channel ထဲ Add လုပ်ပါ\n"
                "2. Channel ID (`-100...`) ကို ကူးယူပါ\n"
                "3. /setchannel နဲ့ ထည့်ပါ\n\n"
                "သို့မဟုတ် Channel ထဲက message တစ်ခုကို Saved Messages ဆီ Forward လုပ်ပြီး ID ကြည့်နိုင်ပါတယ်။"
            )
        else:
            await query.message.reply_text(f"❌ အမှား: `{err[:250]}`")


# ──────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, message: Message):
    track_user(message.from_user)
    text = (
        "👋 **All-in-One Video + Channel Post Bot** မှ ကြိုဆိုပါတယ်!\n\n"
        "📥 **အသုံးပြုနည်း**\n"
        "1. `/setchannel` → သင့် Channel username သို့မဟုတ် ID သတ်မှတ်\n"
        "2. Bot ကို Channel မှာ **Admin** ပေး (Post Messages)\n"
        "3. YouTube / FB / IG / TikTok / Pinterest ... Link ပို့\n"
        "4. Video ပေါ်လာရင် **✏️ Edit** နဲ့ **📤 Post** သုံး\n\n"
        "⚡ **Supported Platforms**\n"
        "• YouTube • Facebook • Instagram • TikTok\n"
        "• Pinterest • Twitter/X • Reddit • Threads\n"
        "• Vimeo • Dailymotion • Twitch • Rumble\n"
        "• LinkedIn • Bilibili • SoundCloud • Streamable\n\n"
        "• Caption ပြင်နိုင် • Channel ပေါ် တိုက်ရိုက်တင်နိုင်\n\n"
        f"— {CREDIT}"
    )
    await message.reply_text(text)


@app.on_message(filters.command("help") & filters.private)
async def cmd_help(client: Client, message: Message):
    await message.reply_text(
        "📖 **အသုံးပြုနည်း**\n\n"
        "`/setchannel` — Channel username သို့မဟုတ် ID သတ်မှတ်\n"
        "`/mychannel` — လက်ရှိ Channel ကြည့်\n"
        "`/users` — (Admin only) သုံးသူစာရင်း\n\n"
        "Link ပို့ → Video ပေါ်လာရင်\n"
        "• ✏️ **Edit** = Caption ပြင်\n"
        "• 📤 **Post** = Channel ပေါ် တင်\n\n"
        f"— {CREDIT}"
    )


@app.on_message(filters.command("setchannel") & filters.private)
async def cmd_setchannel(client: Client, message: Message):
    track_user(message.from_user)
    WAITING_CHANNEL.add(message.from_user.id)
    await message.reply_text(
        "📢 **Channel သတ်မှတ်ရန်**\n\n"
        "• Public Channel ဆိုရင် → `@mychannel` သို့မဟုတ် `mychannel`\n"
        "• Private Channel ဆိုရင် → Channel ID (`-100xxxxxxxxxx`)\n\n"
        "Channel ID ရယူနည်း: @userinfobot ကို Channel ထဲ Add လုပ်ပါ။\n\n"
        "ပယ်ဖျက်ချင်ရင် `/cancel` ရိုက်ပါ။"
    )


@app.on_message(filters.command("mychannel") & filters.private)
async def cmd_mychannel(client: Client, message: Message):
    track_user(message.from_user)
    ch = channels_db.get(str(message.from_user.id))
    if ch:
        display = f"`{ch}`" if str(ch).startswith("-") else f"@{ch}"
        await message.reply_text(f"လက်ရှိ Channel → {display}\n\nပြောင်းချင်ရင် /setchannel ရိုက်ပါ။")
    else:
        await message.reply_text("Channel မသတ်မှတ်ရသေးပါ။\n/setchannel ရိုက်ပါ။")


@app.on_message(filters.command("users") & filters.private)
async def cmd_users(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("ဒီ command ကို **Admin** ပဲ သုံးလို့ရပါတယ်။")
        return

    if not users_db:
        await message.reply_text("User မရှိသေးပါ။")
        return

    text = f"👥 **သုံးသူစုစုပေါင်း:** {len(users_db)}\n\n"
    for i, (uid, info) in enumerate(list(users_db.items())[:40]):
        uname = f"@{info['username']}" if info.get("username") else "—"
        text += f"{i+1}. {info.get('first_name', '')} ({uname}) — `{info['id']}`\n"

    if len(users_db) > 40:
        text += f"\n... နောက်ထပ် {len(users_db)-40} ယောက် ရှိသေးသည်"

    await message.reply_text(text)


@app.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client: Client, message: Message):
    uid = message.from_user.id
    WAITING_CAPTION.discard(uid)
    WAITING_CHANNEL.discard(uid)
    await message.reply_text("ပယ်ဖျက်လိုက်ပါပြီ။")


# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 All-in-One + Channel Post Bot starting | {CREDIT}")
    app.run()
