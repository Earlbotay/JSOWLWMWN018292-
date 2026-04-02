import os
import time
import logging
from aiohttp import web

from config import BOT_NAME, SERVER_START_TIME, MAX_RUNTIME_SECONDS

logger = logging.getLogger(__name__)

STYLE = """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;display:flex;justify-content:center;align-items:center;padding:1rem}
.box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:2rem;max-width:480px;width:100%}
h1{color:#58a6ff;text-align:center;margin-bottom:.5rem;font-size:1.1rem;letter-spacing:1px}
.sub{text-align:center;color:#8b949e;margin-bottom:1.5rem;font-size:.85rem}
label{display:block;margin-bottom:.5rem;color:#8b949e;font-size:.9rem}
input[type=text]{width:100%;padding:.75rem;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:1.2rem;margin-bottom:1rem;text-transform:uppercase;letter-spacing:3px;text-align:center;font-weight:bold}
input[type=text]:focus{outline:none;border-color:#58a6ff}
button,.btn{display:block;width:100%;padding:.75rem;background:#238636;color:#fff;border:none;border-radius:6px;font-size:1rem;cursor:pointer;text-align:center;text-decoration:none;margin-bottom:.5rem}
button:hover,.btn:hover{background:#2ea043}
.info{text-align:center;color:#8b949e;font-size:.8rem;margin-top:1rem}
.err{background:#490202;border:1px solid #f85149;color:#f85149;padding:.75rem;border-radius:6px;margin-bottom:1rem;text-align:center}
.file-card{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:1rem;margin-bottom:.75rem}
.file-card h3{color:#c9d1d9;font-size:.95rem;margin-bottom:.25rem;word-break:break-all}
.file-card .size{color:#8b949e;font-size:.8rem;margin-bottom:.5rem}
.file-card .btn{background:#1f6feb;font-size:.9rem;padding:.5rem}
.file-card .btn:hover{background:#388bfd}
.empty{text-align:center;color:#8b949e;padding:2rem 0}
</style>"""


def _head(title="EARL STORE"):
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title>{STYLE}</head><body><div class="box">'
    )


TAIL = "</div></body></html>"


def _countdown():
    left = max(0, MAX_RUNTIME_SECONDS - (time.time() - SERVER_START_TIME))
    h, r = divmod(int(left), 3600)
    m, _ = divmod(r, 60)
    return f"{h}h {m}m"


def _fmt_size(n):
    if n >= 1073741824:
        return f"{n / 1073741824:.1f} GB"
    if n >= 1048576:
        return f"{n / 1048576:.1f} MB"
    return f"{n / 1024:.1f} KB"


async def index(request):
    error = request.query.get("error", "")
    err_html = f'<div class="err">{error}</div>' if error else ""
    html = (
        _head()
        + '<h1>\u2728 EARL STORE \u2014 BUILD APK \u2728</h1>'
        + '<p class="sub">Download Portal</p>'
        + err_html
        + '<form action="/verify" method="post">'
        + '<label>Enter your Code ID</label>'
        + '<input type="text" name="code" placeholder="A B C 1 2 3" maxlength="6" required autofocus>'
        + '<button type="submit">Continue \u2192</button>'
        + '</form>'
        + f'<p class="info">\U0001f4a1 Get your Code ID from the Telegram bot<br>\u23f3 Portal active: {_countdown()}</p>'
        + TAIL
    )
    return web.Response(text=html, content_type="text/html")


async def verify(request):
    data = await request.post()
    code = data.get("code", "").strip().upper()
    code_ids = request.app["code_ids"]
    if code not in code_ids:
        raise web.HTTPFound("/?error=Invalid+Code+ID")
    raise web.HTTPFound(f"/my/{code}")


async def my_downloads(request):
    code = request.match_info["code"].upper()
    code_ids = request.app["code_ids"]
    if code not in code_ids:
        raise web.HTTPFound("/?error=Invalid+Code+ID")

    info = code_ids[code]
    uid = info["user_id"]
    username = info.get("username", "User")
    download_files = request.app["download_files"]

    user_files = []
    for token, finfo in download_files.items():
        if finfo.get("user_id") == uid and os.path.exists(finfo["path"]):
            user_files.append((token, finfo))

    files_html = ""
    if user_files:
        for token, finfo in user_files:
            size = os.path.getsize(finfo["path"])
            files_html += (
                '<div class="file-card">'
                f'<h3>\U0001f4e6 {finfo["filename"]}</h3>'
                f'<p class="size">{_fmt_size(size)}</p>'
                f'<a class="btn" href="/dl/{token}">\u2b07\ufe0f Download</a>'
                '</div>'
            )
    else:
        files_html = '<div class="empty">\U0001f4ed No files available<br><small>Files appear here when output exceeds 2GB</small></div>'

    html = (
        _head()
        + '<h1>\u2728 EARL STORE \u2014 BUILD APK \u2728</h1>'
        + f'<p class="sub">@{username} \u2014 Downloads</p>'
        + files_html
        + f'<p class="info">\U0001f511 Code: {code}<br>\u23f3 Portal active: {_countdown()}</p>'
        + TAIL
    )
    return web.Response(text=html, content_type="text/html")


async def download_file(request):
    token = request.match_info["token"]
    dl_files = request.app["download_files"]
    info = dl_files.get(token)

    if not info or not os.path.exists(info["path"]):
        html = (
            _head()
            + '<h1>\u2728 EARL STORE \u2014 BUILD APK \u2728</h1>'
            + '<div class="err">File not found or expired.</div>'
            + '<a class="btn" href="/">\u2190 Back</a>'
            + TAIL
        )
        return web.Response(text=html, content_type="text/html", status=404)

    return web.FileResponse(
        info["path"],
        headers={"Content-Disposition": f'attachment; filename="{info["filename"]}"'})


def create_web_app(code_ids, download_files):
    app = web.Application()
    app["code_ids"] = code_ids
    app["download_files"] = download_files

    app.router.add_get("/", index)
    app.router.add_post("/verify", verify)
    app.router.add_get("/my/{code}", my_downloads)
    app.router.add_get("/dl/{token}", download_file)

    return app
