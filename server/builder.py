import asyncio
import os
import re
import shutil
import logging
import aiohttp

logger = logging.getLogger(__name__)


async def run_cmd(cmd, cwd=None, timeout=1200):
    env = os.environ.copy()
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd, env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", "Build timeout exceeded"
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def setup_java(version):
    homes = {
        "17": "/usr/lib/jvm/java-17-openjdk-amd64",
        "11": "/usr/lib/jvm/java-11-openjdk-amd64",
        "8":  "/usr/lib/jvm/java-8-openjdk-amd64",
    }
    home = homes.get(version, homes["17"])
    if os.path.exists(home):
        os.environ["JAVA_HOME"] = home
        os.environ["PATH"] = f"{home}/bin:{os.environ['PATH']}"
        return True
    code, _, _ = await run_cmd(f"sudo apt-get update -qq && sudo apt-get install -y -qq openjdk-{version}-jdk")
    if code == 0 and os.path.exists(home):
        os.environ["JAVA_HOME"] = home
        os.environ["PATH"] = f"{home}/bin:{os.environ['PATH']}"
        return True
    return False


async def setup_android_sdk(compile_sdk=None, build_tools=None):
    ah = os.environ.get("ANDROID_HOME", "/usr/local/lib/android/sdk")
    sm = f"{ah}/cmdline-tools/latest/bin/sdkmanager"
    if not os.path.exists(sm):
        sm = f"{ah}/tools/bin/sdkmanager"
    cmds = ['echo "y" | ' + sm + " --licenses 2>/dev/null || true"]
    if compile_sdk:
        cmds.append(f'echo "y" | {sm} "platforms;android-{compile_sdk}"')
    if build_tools:
        cmds.append(f'echo "y" | {sm} "build-tools;{build_tools}"')
    for c in cmds:
        await run_cmd(c, timeout=300)


async def setup_flutter(version):
    fdir = "/tmp/flutter_sdk"
    if os.path.exists(fdir):
        shutil.rmtree(fdir, ignore_errors=True)
    branch = "stable" if (version == "stable" or not re.match(r"\d+\.\d+\.\d+", version)) else version
    code, _, err = await run_cmd(
        f"git clone https://github.com/flutter/flutter.git -b {branch} --depth 1 {fdir}",
        timeout=300,
    )
    if code != 0:
        logger.error(f"Flutter clone failed: {err}")
        return False
    os.environ["PATH"] = f"{fdir}/bin:{os.environ['PATH']}"
    await run_cmd("flutter precache --android", timeout=300)
    await run_cmd("yes | flutter doctor --android-licenses 2>/dev/null || true", timeout=120)
    return True


async def build_native(project_dir, config):
    logs = []
    await setup_java(config.get("java_version", "11"))
    logs.append(f"Java {config.get('java_version','11')} ready")
    await setup_android_sdk(config.get("compile_sdk"), config.get("build_tools"))
    logs.append("Android SDK ready")

    gradlew = os.path.join(project_dir, "gradlew")
    if os.path.exists(gradlew):
        await run_cmd(f"chmod +x {gradlew}")

    code, out, err = await run_cmd("./gradlew assembleDebug --stacktrace", cwd=project_dir)
    logs.append(f"assembleDebug: {'OK' if code == 0 else 'FAIL'}")
    if code != 0:
        return {"success": False, "error": f"Debug build failed:\n{err[-3000:]}\n{out[-1000:]}", "logs": logs}

    code2, out2, err2 = await run_cmd("./gradlew assembleRelease --stacktrace", cwd=project_dir)
    logs.append(f"assembleRelease: {'OK' if code2 == 0 else 'FAIL'}")

    code3, out3, err3 = await run_cmd("./gradlew bundleRelease --stacktrace", cwd=project_dir)
    logs.append(f"bundleRelease: {'OK' if code3 == 0 else 'FAIL'}")

    files = []
    out_base = os.path.join(project_dir, "app", "build", "outputs")
    if os.path.exists(out_base):
        for root, _, fnames in os.walk(out_base):
            for fn in fnames:
                if fn.endswith((".apk", ".aab")):
                    files.append(os.path.join(root, fn))
    if not files:
        combined = f"{err[-2000:]}\n{err2[-2000:]}\n{err3[-2000:]}"
        return {"success": False, "error": f"No output files found.\n{combined}", "logs": logs}
    return {"success": True, "files": files, "logs": logs}


async def build_flutter(project_dir, config):
    logs = []
    await setup_java(config.get("java_version", "17"))
    logs.append(f"Java {config.get('java_version','17')} ready")
    ok = await setup_flutter(config.get("flutter_version", "stable"))
    if not ok:
        return {"success": False, "error": "Flutter SDK install failed", "logs": logs}
    logs.append(f"Flutter {config.get('flutter_version','stable')} ready")

    gw = os.path.join(project_dir, "android", "gradlew")
    if os.path.exists(gw):
        await run_cmd(f"chmod +x {gw}")

    code, out, err = await run_cmd("flutter pub get", cwd=project_dir, timeout=300)
    logs.append(f"pub get: {'OK' if code == 0 else 'FAIL'}")
    if code != 0:
        return {"success": False, "error": f"flutter pub get failed:\n{err[-3000:]}\n{out[-1000:]}", "logs": logs}

    code, out, err = await run_cmd("flutter build apk --debug", cwd=project_dir)
    logs.append(f"apk debug: {'OK' if code == 0 else 'FAIL'}")
    if code != 0:
        return {"success": False, "error": f"Debug build failed:\n{err[-3000:]}\n{out[-1000:]}", "logs": logs}

    code2, out2, err2 = await run_cmd("flutter build apk --release", cwd=project_dir)
    logs.append(f"apk release: {'OK' if code2 == 0 else 'FAIL'}")

    code3, out3, err3 = await run_cmd("flutter build appbundle --release", cwd=project_dir)
    logs.append(f"appbundle: {'OK' if code3 == 0 else 'FAIL'}")

    files = []
    for search_root in [
        os.path.join(project_dir, "build", "app", "outputs"),
        os.path.join(project_dir, "build", "outputs"),
    ]:
        if os.path.exists(search_root):
            for root, _, fnames in os.walk(search_root):
                for fn in fnames:
                    if fn.endswith((".apk", ".aab")):
                        files.append(os.path.join(root, fn))
    if not files:
        combined = f"{err[-2000:]}\n{err2[-2000:]}\n{err3[-2000:]}"
        return {"success": False, "error": f"No output files found.\n{combined}", "logs": logs}
    return {"success": True, "files": files, "logs": logs}


async def build_project(project_dir, project_info):
    t = project_info["type"]
    c = project_info["config"]
    if t == "native":
        return await build_native(project_dir, c)
    if t == "flutter":
        return await build_flutter(project_dir, c)
    return {"success": False, "error": "Unknown project type"}


async def upload_to_gofile(filepath):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.gofile.io/servers") as r:
                data = await r.json()
                server = data["data"]["servers"][0]["name"]
            url = f"https://{server}.gofile.io/contents/uploadfile"
            form = aiohttp.FormData()
            form.add_field("file", open(filepath, "rb"), filename=os.path.basename(filepath))
            async with s.post(url, data=form) as r:
                res = await r.json()
                if res.get("status") == "ok":
                    return res["data"]["downloadPage"]
    except Exception as e:
        logger.error(f"GoFile upload failed: {e}")
    return None
