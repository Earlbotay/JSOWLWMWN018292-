import asyncio
import logging
import os
import time
import zipfile
import shutil
import random
import string
import uuid
import re
import aiohttp
from aiohttp import web as aio_web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

from config import (
    BOT_TOKEN, OWNER_TG_ID, CHANNEL_ID, CHANNEL_LINK,
    PAT_TOKEN, PRIVATE_REPO, VIDEO_URL,
    BOT_NAME, OWNER_USERNAME, COUNTRY_FLAG,
    SERVER_START_TIME, MAX_RUNTIME_SECONDS, TEMP_DIR,
    USE_LOCAL_API, LOCAL_API_URL,
    WEB_PORT,
)
from data_manager import DataManager
from queue_manager import QueueManager
from detector import extract_zip, detect_project
from builder import build_project, upload_to_gofile
from web_server import create_web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Global state ─────────────────────────────────────────
dm = DataManager(PAT_TOKEN, PRIVATE_REPO)
qm = QueueManager()
video_file_id = None
building = False
build_task = None
shutdown_event = asyncio.Event()
media_group_cache = {}
bot_username = ""

# ── Web / Tunnel state ──────────────────────────────────
code_ids = {}          # code -> {user_id, username, chat_id}
download_files = {}    # token -> {path, filename, user_id}
tunnel_url = None
tunnel_proc = None
application = None
DOWNLOAD_DIR = os.path.join(TEMP_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ── Helpers ──────────────────────────────────────────────
def countdown():
    left = max(0, MAX_RUNTIME_SECONDS - (time.time() - SERVER_START_TIME))
    h, r = divmod(int(left), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def main_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f3d7 BUILDING APK", callback_data="building"),
            InlineKeyboardButton("\u23f3 QUEUE", callback_data="queue"),
        ],
        [
            InlineKeyboardButton("\U0001f4d6 GUIDE", callback_data="guide"),
            InlineKeyboardButton("\U0001f465 TOTAL USER", callback_data="total_user"),
        ],
        [InlineKeyboardButton("\U0001f451 OWNER", url=f"https://t.me/{OWNER_USERNAME}")],
    ])


def back_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f519 BACK", callback_data="back")]
    ])


def join_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f4e2 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("\u2705 Already Joined", callback_data="check_join")],
    ])


async def edit_msg(query, text, kb):
    try:
        if query.message and (query.message.video or query.message.animation):
            await query.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.warning(f"edit_msg failed: {e}")


async def check_join(bot, user_id):
    if not CHANNEL_ID:
        return True
    try:
        m = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False


# ── Code ID helpers ──────────────────────────────────────
def generate_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in code_ids:
            return code


def get_or_create_code(user_id, username, chat_id):
    for code, info in code_ids.items():
        if info["user_id"] == user_id:
            return code
    code = generate_code()
    code_ids[code] = {"user_id": user_id, "username": username, "chat_id": chat_id}
    return code


async def start_text(user_id=None):
    stats = await dm.get_build_stats()
    total = stats.get("total_success", 0)

    web_line = ""
    if tunnel_url and user_id:
        user_code = None
        for code, info in code_ids.items():
            if info["user_id"] == user_id:
                user_code = code
                break
        if user_code:
            web_line = (
                "\u25c6 \U0001f310 <b>WEB</b> : <a href=\"" + tunnel_url + "\">Download Portal</a>\n"
                "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                "\u25c6 \U0001f511 <b>CODE</b> : <code>" + user_code + "</code>\n"
                "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            )

    return (
        "<blockquote>"
        "\u2728 " + BOT_NAME + " \u2728\n"
        "\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u250c\n"
        "\u25c6 \U0001f451 <b>OWNER</b> : @" + OWNER_USERNAME + "\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 \U0001f5a5 <b>STATUS BOT</b> : ONLINE\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 \U0001f4c2 <b>UPLOAD BY</b> : GITHUB\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 \u23f1 <b>RESTART IN</b> : " + countdown() + "\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 \u2705 <b>BUILD APK</b> : " + str(total) + "\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        + web_line +
        "\u2514\n"
        "" + COUNTRY_FLAG + " CREATOR'S COUNTRY : MALAYSIA " + COUNTRY_FLAG + "\n"
        "Bot auto-compile APK \u2014 Native, Flutter & Smali"
        "</blockquote>"
    )


# ── /start ───────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await dm.register_user({
        "user_id": user.id,
        "username": user.username or "",
        "first_name": user.first_name or "",
    })
    joined = await check_join(ctx.bot, user.id)
    if not joined:
        txt = (
            "<blockquote>\u2728 " + BOT_NAME + " \u2728\n\n"
            "\u274c Please join our channel first!</blockquote>"
        )
        await update.message.reply_text(txt, parse_mode="HTML", reply_markup=join_kb())
        return

    # Generate code ID for download portal
    get_or_create_code(user.id, user.username or user.first_name or str(user.id), update.effective_chat.id)

    txt = await start_text(user.id)
    global video_file_id
    video_url = VIDEO_URL
    try:
        if video_file_id:
            await update.message.reply_video(
                video=video_file_id, caption=txt,
                parse_mode="HTML", reply_markup=main_kb(),
            )
        else:
            msg = await update.message.reply_video(
                video=video_url, caption=txt,
                parse_mode="HTML", reply_markup=main_kb(),
            )
            if msg.video:
                video_file_id = msg.video.file_id
    except Exception as e:
        logger.warning(f"Video send failed ({e}), sending text only")
        await update.message.reply_text(txt, parse_mode="HTML", reply_markup=main_kb())


# ── Callback handlers ────────────────────────────────────
async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data

    if d == "check_join":
        if await check_join(ctx.bot, q.from_user.id):
            get_or_create_code(q.from_user.id, q.from_user.username or q.from_user.first_name or str(q.from_user.id), q.message.chat_id)
            await edit_msg(q, await start_text(q.from_user.id), main_kb())
        else:
            await q.answer("\u274c You haven't joined yet!", show_alert=True)
    elif d == "building":
        await show_building(q)
    elif d == "queue":
        await show_queue(q)
    elif d == "guide":
        await show_guide(q)
    elif d == "total_user":
        await show_users(q)
    elif d == "back":
        await edit_msg(q, await start_text(q.from_user.id), main_kb())


async def show_building(q):
    stats = await dm.get_build_stats()
    status = "\U0001f528 Compiling..." if building else "\u2705 No active process"
    cur = ""
    if qm.current:
        cur = "\n\u25c6 <b>User</b>: @" + str(qm.current.get("username", "?"))

    hist = ""
    for i, h in enumerate(stats.get("recent_success", [])[:5], 1):
        hist += (
            "\n" + str(i) + ". @" + h["username"]
            + " \u2014 " + h["project_name"]
            + " (" + h["project_type"].upper() + ")"
            + " \u2014 " + h["time"]
        )
    if not hist:
        hist = "\nNo records yet"

    sn = stats.get("total_smali_native", 0)
    sf = stats.get("total_smali_flutter", 0)
    # Backward compat: old total_smali
    old_smali = stats.get("total_smali", 0)
    if old_smali and not sn and not sf:
        sn = old_smali

    txt = (
        "<blockquote>\u2728 " + BOT_NAME + " \u2728\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4ca <b>BUILD STATUS</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 <b>Status</b>: " + status + cur + "\n\n"
        "\U0001f4bb <b>Source Code Level</b>\n"
        "\u25c6 Native: " + str(stats.get("total_native", 0)) + "\n"
        "\u25c6 Flutter: " + str(stats.get("total_flutter", 0)) + "\n\n"
        "\U0001f9ec <b>Smali Level</b>\n"
        "\u25c6 Native: " + str(sn) + "\n"
        "\u25c6 Flutter: " + str(sf) + "\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4cb <b>SUCCESSFUL BUILD HISTORY</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
        + hist + "</blockquote>"
    )
    await edit_msg(q, txt, back_kb())


async def show_queue(q):
    sz = qm.get_size()
    cur = ("@" + qm.current["username"]) if qm.current else "None"
    txt = (
        "<blockquote>\u2728 " + BOT_NAME + " \u2728\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u23f3 <b>BUILD QUEUE</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        "\u25c6 <b>Users in queue</b>: " + str(sz) + "\n"
        "\u25c6 <b>Currently compiling</b>: " + cur + "</blockquote>"
    )
    await edit_msg(q, txt, back_kb())


async def show_guide(q):
    limit_text = "No file size limit" if USE_LOCAL_API else "Limit 50MB (larger \u2192 GoFile)"
    txt = (
        "<blockquote>\u2728 " + BOT_NAME + " \u2728\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4d6 <b>GUIDE</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        "1\ufe0f\u20e3 Send <b>.zip</b> file to the bot\n"
        "2\ufe0f\u20e3 Reply to the file with /build\n"
        "3\ufe0f\u20e3 Bot auto-detects & compiles\n"
        "4\ufe0f\u20e3 Wait ~5-20 minutes\n"
        "5\ufe0f\u20e3 Bot sends APK + AAB\n\n"
        "\u26a0\ufe0f <b>NOTE:</b>\n"
        "\u2022 <b>Source Code</b>: Android Native / Flutter\n"
        "\u2022 <b>Smali</b>: apktool (auto-detect Native/Flutter)\n"
        "\u2022 <b>Smali APKS</b>: Include splits/ folder for split APK output\n"
        "\u2022 Release APK/AAB is unsigned\n"
        "\u2022 " + limit_text + "\n"
        "\u2022 One build at a time</blockquote>"
    )
    await edit_msg(q, txt, back_kb())


async def show_users(q):
    count = await dm.get_user_count()
    txt = (
        "<blockquote>\u2728 " + BOT_NAME + " \u2728\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f465 <b>TOTAL USERS</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        "\u25c6 <b>Total users</b>: " + str(count) + "</blockquote>"
    )
    await edit_msg(q, txt, back_kb())


# ── File handler ─────────────────────────────────────────
async def on_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc and doc.file_name and doc.file_name.lower().endswith(".zip"):
        await update.message.reply_text(
            "<blockquote>\U0001f4e6 File <b>" + doc.file_name + "</b> received!\n\n"
            "Reply to this file with /build to start compiling.</blockquote>",
            parse_mode="HTML",
        )


# ── Media group tracker (for album /foward) ─────────────
async def on_message_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg and msg.media_group_id:
        gid = msg.media_group_id
        if gid not in media_group_cache:
            media_group_cache[gid] = []
        if msg.message_id not in media_group_cache[gid]:
            media_group_cache[gid].append(msg.message_id)
        if len(media_group_cache) > 500:
            oldest = list(media_group_cache.keys())[0]
            del media_group_cache[oldest]


# ── /build ───────────────────────────────────────────────
async def cmd_build(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    await dm.register_user({
        "user_id": user.id,
        "username": user.username or "",
        "first_name": user.first_name or "",
    })
    joined = await check_join(ctx.bot, user.id)
    if not joined:
        await msg.reply_text(
            "<blockquote>\u274c Please join the channel first!</blockquote>",
            parse_mode="HTML", reply_markup=join_kb(),
        )
        return
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text(
            "<blockquote>\u274c Please reply to a .zip file with /build</blockquote>",
            parse_mode="HTML",
        )
        return
    doc = msg.reply_to_message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".zip"):
        await msg.reply_text(
            "<blockquote>\u274c File must be .zip</blockquote>",
            parse_mode="HTML",
        )
        return

    # Ensure code ID exists
    get_or_create_code(user.id, user.username or user.first_name or str(user.id), msg.chat_id)

    req = {
        "user_id": user.id,
        "username": user.username or user.first_name or str(user.id),
        "file_id": doc.file_id,
        "file_name": doc.file_name,
        "chat_id": msg.chat_id,
    }
    pos = qm.add(req)
    if pos == 0:
        status_msg = await msg.reply_text(
            "<blockquote>\u2705 Project received! Detecting project type...</blockquote>",
            parse_mode="HTML",
        )
        global build_task
        build_task = asyncio.create_task(process_build(ctx.bot, req, status_msg))
    else:
        await msg.reply_text(
            "<blockquote>\u23f3 In queue. Position: #" + str(pos) + "\nPlease wait for your turn!</blockquote>",
            parse_mode="HTML",
        )


# ── Build processor ──────────────────────────────────────
async def edit_status(bot, msg, text):
    try:
        await bot.edit_message_text(
            chat_id=msg.chat_id, message_id=msg.message_id,
            text="<blockquote>" + text + "</blockquote>",
            parse_mode="HTML",
        )
    except Exception:
        pass


async def notify_channel_success(bot, req, fname, display_type):
    if not CHANNEL_ID:
        return
    try:
        queue_count = qm.get_size()
        txt = (
            "<blockquote><b>BUILD SUCCESSFUL</b>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "\u25c6 <b>User</b>: @" + req["username"] + "\n"
            "\u25c6 <b>APK</b>: " + fname + "\n"
            "\u25c6 <b>Type</b>: " + display_type + "\n"
            "\u25c6 <b>Queue</b>: " + str(queue_count) + "\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n<b>Build By:</b>"
            " @" + bot_username + "</blockquote>"
        )
        await bot.send_message(
            chat_id=CHANNEL_ID, text=txt, parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Channel notify failed: {e}")


async def process_build(bot, req, status_msg):
    global building, build_task
    building = True
    chat_id = req["chat_id"]
    fname = req["file_name"]
    pname = fname.rsplit(".", 1)[0]
    bdir = os.path.join(TEMP_DIR, str(req["user_id"]), str(int(time.time())))
    os.makedirs(bdir, exist_ok=True)
    cancelled = False

    try:
        if shutdown_event.is_set():
            cancelled = True
            return

        # Download
        await edit_status(bot, status_msg, "\U0001f4e5 Downloading file...")
        tg_file = await bot.get_file(req["file_id"], read_timeout=600)
        zip_path = os.path.join(bdir, fname)
        await tg_file.download_to_drive(zip_path, read_timeout=600)

        if shutdown_event.is_set():
            cancelled = True
            return

        # Extract
        await edit_status(bot, status_msg, "\U0001f4e6 Extracting zip...")
        project_dir = extract_zip(zip_path, bdir)

        if shutdown_event.is_set():
            cancelled = True
            return

        # Detect
        await edit_status(bot, status_msg, "\U0001f50d Detecting project type...")
        info = detect_project(project_dir)
        if not info:
            await edit_status(bot, status_msg, "\u274c Unsupported project.\nEnsure it's Android Native, Flutter or Smali (apktool).")
            return

        ptype = info["type"]
        stat_type = ptype
        display_type = ptype.upper()

        # Smali sub-type
        if ptype == "smali":
            sub = info["config"].get("sub_type", "native")
            display_type = "SMALI (" + sub.upper() + ")"
            stat_type = "smali_" + sub

        await edit_status(
            bot, status_msg,
            "Detected: <b>" + display_type + "</b>\nCompiling & Building ... (~5-20 min)",
        )

        if shutdown_event.is_set():
            cancelled = True
            return

        # Build
        result = await build_project(project_dir, info)

        if shutdown_event.is_set():
            cancelled = True
            return

        if result["success"]:
            await edit_status(bot, status_msg, "\u2705 Build successful! Sending files...")

            is_apks = result.get("output_format") == "apks"
            is_signed = result.get("signed", False)

            out_path = os.path.join(bdir, pname + "_output.zip")
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fp in result["files"]:
                    zf.write(fp, os.path.basename(fp))
            out_name = pname + "_output.zip"

            fsize = os.path.getsize(out_path)
            if ptype == "smali":
                fmt = "APKS" if is_apks else "APK"
                sign_note = f"\u2705 {fmt} signed with debug key." if is_signed else f"\u26a0\ufe0f {fmt} is unsigned."
            else:
                sign_note = "\u26a0\ufe0f Release APK/AAB is unsigned."
            caption = (
                "<blockquote><b>Build Successful!</b>\n\n"
                "<b>Project</b>: " + fname + "\n"
                "<b>Type</b>: " + display_type + "\n\n"
                "" + sign_note + "\n\n"
                "<b>BUILD BY @Earlxz</b></blockquote>"
            )
            max_upload = 2000 * 1024 * 1024 if USE_LOCAL_API else 50 * 1024 * 1024
            if fsize <= max_upload:
                with open(out_path, "rb") as f:
                    await bot.send_document(
                        chat_id=chat_id, document=f,
                        filename=out_name,
                        caption=caption, parse_mode="HTML",
                        read_timeout=600, write_timeout=600,
                    )
            else:
                # >2GB: upload to GoFile + store for web portal
                gofile_link = await upload_to_gofile(out_path)

                # Store locally for web portal
                dl_text = ""
                if tunnel_url:
                    token = uuid.uuid4().hex[:8]
                    dl_path = os.path.join(DOWNLOAD_DIR, token + "_" + out_name)
                    shutil.move(out_path, dl_path)
                    download_files[token] = {
                        "path": dl_path,
                        "filename": out_name,
                        "user_id": req["user_id"],
                    }
                    left = max(0, MAX_RUNTIME_SECONDS - (time.time() - SERVER_START_TIME))
                    h, r = divmod(int(left), 3600)
                    m, _ = divmod(r, 60)
                    user_code = ""
                    for code, info_c in code_ids.items():
                        if info_c["user_id"] == req["user_id"]:
                            user_code = code
                            break
                    dl_text = (
                        "\n\U0001f310 <b>Web Portal</b>: " + tunnel_url + "\n"
                        "\U0001f511 Code: <code>" + user_code + "</code>\n"
                        "\u23f3 Link aktif: " + str(h) + "j " + str(m) + "m\n"
                    )

                if gofile_link:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "<blockquote><b>Build Successful!</b>\n\n"
                            "" + fname + " (" + display_type + ")\n\n"
                            "\U0001f4e5 <b>GoFile</b>: " + gofile_link + "\n"
                            + dl_text +
                            "\n<b>BUILD BY @Earlxz</b></blockquote>"
                        ),
                        parse_mode="HTML",
                    )
                elif dl_text:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "<blockquote><b>Build Successful!</b>\n\n"
                            "" + fname + " (" + display_type + ")\n\n"
                            "File too large for Telegram.\n"
                            + dl_text +
                            "\n<b>BUILD BY @Earlxz</b></blockquote>"
                        ),
                        parse_mode="HTML",
                    )
                else:
                    await bot.send_message(
                        chat_id=chat_id,
                        text="<blockquote>\u274c File too large & upload failed.</blockquote>",
                        parse_mode="HTML",
                    )

            # Record successful build
            await dm.add_build_history({
                "user_id": req["user_id"], "username": req["username"],
                "project_name": fname, "project_type": stat_type, "success": True,
            })
            await notify_channel_success(bot, req, fname, display_type)
        else:
            err_txt = result.get("error", "Unknown error")
            err_full = result.get("error_full", err_txt)
            err_log = os.path.join(bdir, "error_log.txt")
            with open(err_log, "w") as f:
                f.write(err_txt)
            err_log_full = os.path.join(bdir, "error_log_full.txt")
            with open(err_log_full, "w") as f:
                f.write(err_full)
            err_zip = os.path.join(bdir, pname + "_error.zip")
            with zipfile.ZipFile(err_zip, "w") as zf:
                zf.write(err_log, "error_log.txt")
                zf.write(err_log_full, "error_log_full.txt")
            with open(err_zip, "rb") as f:
                await bot.send_document(
                    chat_id=chat_id, document=f,
                    filename=pname + "_error.zip",
                    caption=(
                        "<blockquote><b>Build Failed.</b>\n\n"
                        "" + fname + " (" + display_type + ")\n\n"
                        "Check error log in zip.</blockquote>"
                    ),
                    parse_mode="HTML",
                )

    except asyncio.CancelledError:
        cancelled = True
        logger.info("Build cancelled due to server restart")
        try:
            await edit_status(
                bot, status_msg,
                "\u23f8 Build paused \u2014 server restarting.\nWill resume automatically.",
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Build error: {e}", exc_info=True)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="<blockquote>\u274c Error: " + str(e)[:200] + "</blockquote>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    finally:
        building = False
        build_task = None
        shutil.rmtree(bdir, ignore_errors=True)
        if cancelled or shutdown_event.is_set():
            return
        qm.finish_current()
        nxt = qm.get_next()
        if nxt:
            try:
                sm = await bot.send_message(
                    chat_id=nxt["chat_id"],
                    text="<blockquote>\U0001f528 Your turn! Processing...</blockquote>",
                    parse_mode="HTML",
                )
                build_task = asyncio.create_task(process_build(bot, nxt, sm))
            except Exception as e:
                logger.error(f"Next build failed: {e}")


# ── /foward (owner only) — supports media albums ────────
async def cmd_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_TG_ID:
        return
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "<blockquote>\u274c Reply to a message to forward.</blockquote>",
            parse_mode="HTML",
        )
        return

    reply = update.message.reply_to_message
    users = await dm.get_all_users()

    msg_ids = [reply.message_id]
    if reply.media_group_id:
        await asyncio.sleep(2)
        if reply.media_group_id in media_group_cache:
            msg_ids = sorted(media_group_cache[reply.media_group_id])

    ok, fail = 0, 0
    for uid in users:
        if int(uid) == OWNER_TG_ID:
            continue
        try:
            if len(msg_ids) > 1:
                try:
                    await ctx.bot.forward_messages(
                        chat_id=int(uid),
                        from_chat_id=update.effective_chat.id,
                        message_ids=msg_ids,
                    )
                except Exception:
                    for mid in msg_ids:
                        try:
                            await ctx.bot.forward_message(
                                chat_id=int(uid),
                                from_chat_id=update.effective_chat.id,
                                message_id=mid,
                            )
                            await asyncio.sleep(0.05)
                        except Exception:
                            pass
            else:
                await ctx.bot.forward_message(
                    chat_id=int(uid),
                    from_chat_id=update.effective_chat.id,
                    message_id=msg_ids[0],
                )
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)

    await update.message.reply_text(
        "<blockquote>\u2705 <b>Forward complete!</b>\n"
        "\U0001f4e4 Success: " + str(ok) + "\n"
        "\u274c Failed: " + str(fail) + "</blockquote>",
        parse_mode="HTML",
    )


# ── Cloudflare Tunnel ────────────────────────────────────
async def _drain_stderr(proc):
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
    except Exception:
        pass


async def start_cloudflare_tunnel(port):
    global tunnel_url, tunnel_proc
    if not shutil.which("cloudflared"):
        logger.warning("cloudflared not found — web portal disabled")
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        tunnel_proc = proc
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                line = await asyncio.wait_for(proc.stderr.readline(), timeout=2)
                if not line:
                    break
                text = line.decode(errors="replace")
                m = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', text)
                if m:
                    tunnel_url = m.group(0)
                    asyncio.create_task(_drain_stderr(proc))
                    logger.info(f"Cloudflare tunnel: {tunnel_url}")
                    return tunnel_url
            except asyncio.TimeoutError:
                continue
        logger.warning("Could not get tunnel URL")
        proc.kill()
        return None
    except Exception as e:
        logger.error(f"Tunnel error: {e}")
        return None


# ── Respawn ──────────────────────────────────────────────
async def respawn():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        logger.error("GITHUB_REPOSITORY not set - cannot respawn")
        return False
    headers = {
        "Authorization": f"token {PAT_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    try:
        async with aiohttp.ClientSession() as s:
            url = f"https://api.github.com/repos/{repo}/actions/workflows"
            async with s.get(url, headers=headers) as r:
                data = await r.json()
                wf_id = None
                for w in data.get("workflows", []):
                    if "server" in w.get("name", "").lower():
                        wf_id = w["id"]
                        break
            if wf_id:
                d_url = f"https://api.github.com/repos/{repo}/actions/workflows/{wf_id}/dispatches"
                async with s.post(d_url, headers=headers, json={"ref": "main"}) as r:
                    if r.status == 204:
                        logger.info("Respawn triggered!")
                        return True
    except Exception as e:
        logger.error(f"Respawn error: {e}")
    logger.error("Respawn FAILED!")
    return False


async def respawn_timer(app):
    wait = max(0, MAX_RUNTIME_SECONDS - 10)
    await asyncio.sleep(wait)
    logger.info("10 seconds to restart — stopping current build...")
    shutdown_event.set()

    if build_task and not build_task.done():
        build_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(build_task), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

    if qm.current:
        qm.queue.insert(0, qm.current)
        qm.current = None

    logger.info("Saving queue and respawning...")
    await dm.save_queue(qm.to_dict())
    await respawn()
    await asyncio.sleep(5)
    os._exit(0)


# ── Post init ────────────────────────────────────────────
async def post_init(app: Application):
    global bot_username, application
    application = app
    me = await app.bot.get_me()
    bot_username = me.username or ""
    data = await dm.load_queue()
    qm.from_dict(data)

    # Start web server for download portal
    try:
        web_app = create_web_app(code_ids, download_files)
        runner = aio_web.AppRunner(web_app)
        await runner.setup()
        site = aio_web.TCPSite(runner, "0.0.0.0", WEB_PORT)
        await site.start()
        logger.info(f"Web server started on port {WEB_PORT}")
    except Exception as e:
        logger.error(f"Web server failed: {e}")

    # Start Cloudflare tunnel
    await start_cloudflare_tunnel(WEB_PORT)

    asyncio.create_task(respawn_timer(app))
    if USE_LOCAL_API:
        logger.info("EARL STORE BUILD APK bot started! (Local API — no file size limit)")
    else:
        logger.info("EARL STORE BUILD APK bot started!")


# ── Main ─────────────────────────────────────────────────
def main():
    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(600)
        .write_timeout(600)
        .connect_timeout(60)
        .pool_timeout(60)
    )
    if USE_LOCAL_API:
        builder = builder.base_url(f"{LOCAL_API_URL}/bot").base_file_url(f"{LOCAL_API_URL}/file/bot")
    app = builder.post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("build", cmd_build))
    app.add_handler(CommandHandler("foward", cmd_forward))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.ALL, on_message_track), group=-1)
    app.add_handler(MessageHandler(filters.Document.ALL, on_file))
    logger.info("Starting bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
