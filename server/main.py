import asyncio
import logging
import os
import time
import zipfile
import shutil
import aiohttp

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
)
from data_manager import DataManager
from queue_manager import QueueManager
from detector import extract_zip, detect_project
from builder import build_project, upload_to_gofile

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
            InlineKeyboardButton("\U0001f4d6 PANDUAN", callback_data="panduan"),
            InlineKeyboardButton("\U0001f465 TOTAL USER", callback_data="total_user"),
        ],
        [InlineKeyboardButton("\U0001f451 OWNER", url=f"https://t.me/{OWNER_USERNAME}")],
    ])


def back_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f519 KEMBALI", callback_data="back")]
    ])


def join_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f4e2 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("\u2705 Sudah Join", callback_data="check_join")],
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


async def start_text():
    stats = await dm.get_build_stats()
    total = stats.get("total_success", 0)
    return (
        "<blockquote>"
        "\u2728 " + BOT_NAME + " \u2728\n"
        "\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u250c\n"
        "\u25c6 \U0001f464 OWNER : @" + OWNER_USERNAME + "\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 \U0001f5a5 STATUS BOT : ONLINE\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 \U0001f4c2 UPLOAD BY : GITHUB\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 \u23f1 RESTART IN : " + countdown() + "\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 \u2705 BUILD BERJAYA : " + str(total) + "\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u2514\n"
        "" + COUNTRY_FLAG + " CREATOR'S COUNTRY : MALAYSIA " + COUNTRY_FLAG + "\n"
        "Bot auto-compile APK \u2014 Native & Flutter"
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
            "\u274c Sila join channel kami dahulu!</blockquote>"
        )
        await update.message.reply_text(txt, parse_mode="HTML", reply_markup=join_kb())
        return

    txt = await start_text()
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
            await edit_msg(q, await start_text(), main_kb())
        else:
            await q.answer("\u274c Belum join lagi!", show_alert=True)
    elif d == "building":
        await show_building(q)
    elif d == "queue":
        await show_queue(q)
    elif d == "panduan":
        await show_panduan(q)
    elif d == "total_user":
        await show_users(q)
    elif d == "back":
        await edit_msg(q, await start_text(), main_kb())


async def show_building(q):
    stats = await dm.get_build_stats()
    status = "\U0001f528 Sedang compile..." if building else "\u2705 Tiada proses"
    cur = ""
    if qm.current:
        cur = "\n\u25c6 \U0001f464 User: @" + str(qm.current.get("username", "?"))

    hist = ""
    for i, h in enumerate(stats.get("recent_success", [])[:5], 1):
        hist += (
            "\n" + str(i) + ". @" + h["username"]
            + " \u2014 " + h["project_name"]
            + " (" + h["project_type"].upper() + ")"
            + " \u2014 " + h["time"]
        )
    if not hist:
        hist = "\nTiada lagi"

    txt = (
        "<blockquote>\u2728 " + BOT_NAME + " \u2728\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4ca STATUS PEMBINAAN\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u25c6 Status: " + status + cur + "\n"
        "\u25c6 Total Native: " + str(stats.get("total_native", 0)) + "\n"
        "\u25c6 Total Flutter: " + str(stats.get("total_flutter", 0)) + "\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4cb SEJARAH BUILD BERJAYA\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
        + hist + "</blockquote>"
    )
    await edit_msg(q, txt, back_kb())


async def show_queue(q):
    sz = qm.get_size()
    cur = ("@" + qm.current["username"]) if qm.current else "Tiada"
    txt = (
        "<blockquote>\u2728 " + BOT_NAME + " \u2728\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u23f3 ANTRIAN BUILD\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        "\u25c6 User dalam barisan: " + str(sz) + "\n"
        "\u25c6 Sedang compile: " + cur + "</blockquote>"
    )
    await edit_msg(q, txt, back_kb())


async def show_panduan(q):
    txt = (
        "<blockquote>\u2728 " + BOT_NAME + " \u2728\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4d6 PANDUAN\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        "1\ufe0f\u20e3 Hantar file <b>.zip</b> ke bot\n"
        "2\ufe0f\u20e3 Reply file tu dgn /build\n"
        "3\ufe0f\u20e3 Bot auto-detect & compile\n"
        "4\ufe0f\u20e3 Tunggu 5-20 minit\n"
        "5\ufe0f\u20e3 Bot hantar APK + AAB\n\n"
        "\u26a0\ufe0f NOTA:\n"
        "\u2022 Project Android/Flutter sahaja\n"
        "\u2022 Release unsigned \u2014 sign sendiri\n"
        "\u2022 Limit 50MB (lebih \u2192 GoFile)\n"
        "\u2022 Satu build pada satu masa</blockquote>"
    )
    await edit_msg(q, txt, back_kb())


async def show_users(q):
    count = await dm.get_user_count()
    txt = (
        "<blockquote>\u2728 " + BOT_NAME + " \u2728\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f465 JUMLAH PENGGUNA\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        "\u25c6 Total user: " + str(count) + " orang</blockquote>"
    )
    await edit_msg(q, txt, back_kb())


# ── File handler ─────────────────────────────────────────
async def on_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc and doc.file_name and doc.file_name.lower().endswith(".zip"):
        await update.message.reply_text(
            "<blockquote>\U0001f4e6 File <b>" + doc.file_name + "</b> diterima!\n\n"
            "Reply file ini dengan /build untuk mula compile.</blockquote>",
            parse_mode="HTML",
        )


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
            "<blockquote>\u274c Sila join channel dahulu!</blockquote>",
            parse_mode="HTML", reply_markup=join_kb(),
        )
        return
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text(
            "<blockquote>\u274c Sila reply kepada file .zip dengan /build</blockquote>",
            parse_mode="HTML",
        )
        return
    doc = msg.reply_to_message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".zip"):
        await msg.reply_text(
            "<blockquote>\u274c File mestilah .zip</blockquote>",
            parse_mode="HTML",
        )
        return

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
            "<blockquote>\u2705 Project diterima! Mengesan jenis project...</blockquote>",
            parse_mode="HTML",
        )
        asyncio.create_task(process_build(ctx.bot, req, status_msg))
    else:
        await msg.reply_text(
            "<blockquote>\u23f3 Dalam queue. Posisi: #" + str(pos) + "\nTunggu giliran ya!</blockquote>",
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


async def process_build(bot, req, status_msg):
    global building
    building = True
    chat_id = req["chat_id"]
    fname = req["file_name"]
    pname = fname.rsplit(".", 1)[0]
    bdir = os.path.join(TEMP_DIR, str(req["user_id"]), str(int(time.time())))
    os.makedirs(bdir, exist_ok=True)

    try:
        # Download
        await edit_status(bot, status_msg, "\U0001f4e5 Memuat turun file...")
        tg_file = await bot.get_file(req["file_id"])
        zip_path = os.path.join(bdir, fname)
        await tg_file.download_to_drive(zip_path)

        # Extract
        await edit_status(bot, status_msg, "\U0001f4e6 Mengekstrak zip...")
        project_dir = extract_zip(zip_path, bdir)

        # Detect
        await edit_status(bot, status_msg, "\U0001f50d Mengesan jenis project...")
        info = detect_project(project_dir)
        if not info:
            await edit_status(bot, status_msg, "\u274c Project tidak disokong.\nPastikan Android Native atau Flutter.")
            return

        ptype = info["type"]
        await edit_status(
            bot, status_msg,
            "\U0001f4f1 Detected: " + ptype.upper() + "\n\U0001f528 Sedang compile... (~5-20 min)",
        )

        # Build
        result = await build_project(project_dir, info)

        if result["success"]:
            await edit_status(bot, status_msg, "\u2705 Build berjaya! Menghantar file...")
            out_zip = os.path.join(bdir, pname + "_output.zip")
            with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for fp in result["files"]:
                    zf.write(fp, os.path.basename(fp))

            fsize = os.path.getsize(out_zip)
            caption = (
                "<blockquote>\u2705 Build berjaya!\n\n"
                "\U0001f4f1 Project: " + fname + "\n"
                "\U0001f527 Jenis: " + ptype.upper() + "\n\n"
                "\u26a0\ufe0f Release APK/AAB unsigned.</blockquote>"
            )
            if fsize <= 50 * 1024 * 1024:
                with open(out_zip, "rb") as f:
                    await bot.send_document(
                        chat_id=chat_id, document=f,
                        filename=pname + "_output.zip",
                        caption=caption, parse_mode="HTML",
                    )
            else:
                link = await upload_to_gofile(out_zip)
                if link:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "<blockquote>\u2705 Build berjaya!\n\n"
                            "\U0001f4f1 " + fname + " (" + ptype.upper() + ")\n\n"
                            "\U0001f4e5 File besar. Download:\n" + link + "</blockquote>"
                        ),
                        parse_mode="HTML",
                    )
                else:
                    await bot.send_message(
                        chat_id=chat_id,
                        text="<blockquote>\u274c File terlalu besar & upload gagal.</blockquote>",
                        parse_mode="HTML",
                    )
            await dm.add_build_history({
                "user_id": req["user_id"], "username": req["username"],
                "project_name": fname, "project_type": ptype, "success": True,
            })
        else:
            err_txt = result.get("error", "Unknown error")
            err_log = os.path.join(bdir, "error_log.txt")
            with open(err_log, "w") as f:
                f.write(err_txt)
            err_zip = os.path.join(bdir, pname + "_error.zip")
            with zipfile.ZipFile(err_zip, "w") as zf:
                zf.write(err_log, "error_log.txt")
            with open(err_zip, "rb") as f:
                await bot.send_document(
                    chat_id=chat_id, document=f,
                    filename=pname + "_error.zip",
                    caption=(
                        "<blockquote>\u274c Build gagal.\n\n"
                        "\U0001f4f1 " + fname + " (" + ptype.upper() + ")\n\n"
                        "Semak error log dalam zip.</blockquote>"
                    ),
                    parse_mode="HTML",
                )
            await dm.add_build_history({
                "user_id": req["user_id"], "username": req["username"],
                "project_name": fname, "project_type": ptype, "success": False,
            })
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
        shutil.rmtree(bdir, ignore_errors=True)
        qm.finish_current()
        nxt = qm.get_next()
        if nxt:
            try:
                sm = await bot.send_message(
                    chat_id=nxt["chat_id"],
                    text="<blockquote>\U0001f528 Giliran kau! Sedang proses...</blockquote>",
                    parse_mode="HTML",
                )
                asyncio.create_task(process_build(bot, nxt, sm))
            except Exception as e:
                logger.error(f"Next build failed: {e}")


# ── /forward (owner only) ───────────────────────────────
async def cmd_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_TG_ID:
        return
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "<blockquote>\u274c Reply mesej yang nak forward.</blockquote>",
            parse_mode="HTML",
        )
        return
    users = await dm.get_all_users()
    ok, fail = 0, 0
    for uid in users:
        try:
            await update.message.reply_to_message.forward(chat_id=int(uid))
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(
        "<blockquote>\u2705 Forward siap!\n\U0001f4e4 Berjaya: " + str(ok) + "\n\u274c Gagal: " + str(fail) + "</blockquote>",
        parse_mode="HTML",
    )


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
    await asyncio.sleep(MAX_RUNTIME_SECONDS)
    logger.info("Time limit approaching - respawning...")
    await dm.save_queue(qm.to_dict())
    await respawn()
    await asyncio.sleep(10)
    os._exit(0)


# ── Post init ────────────────────────────────────────────
async def post_init(app: Application):
    data = await dm.load_queue()
    qm.from_dict(data)
    asyncio.create_task(respawn_timer(app))
    logger.info("EARL STORE BUILD APK bot started!")


# ── Main ─────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("build", cmd_build))
    app.add_handler(CommandHandler("forward", cmd_forward))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, on_file))
    logger.info("Starting bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
