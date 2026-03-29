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


async def fix_common_issues(project_dir, logs, gradle_subdir=""):
    """Auto-fix common build issues before compilation."""
    gdir = os.path.join(project_dir, gradle_subdir) if gradle_subdir else project_dir
    gradlew = os.path.join(gdir, "gradlew")

    # 1. Fix Windows line endings (CRLF → LF) on key build files
    crlf_fixed = 0
    for root, dirs, fnames in os.walk(gdir):
        dirs[:] = [d for d in dirs if d not in (".git", "build", ".gradle", "node_modules")]
        for fn in fnames:
            if fn in ("gradlew",) or fn.endswith((".gradle", ".properties", ".xml", ".pro", ".kts")):
                fpath = os.path.join(root, fn)
                try:
                    with open(fpath, "rb") as f:
                        content = f.read()
                    if b"\r\n" in content:
                        with open(fpath, "wb") as f:
                            f.write(content.replace(b"\r\n", b"\n"))
                        crlf_fixed += 1
                except Exception:
                    pass
    if crlf_fixed > 0:
        logs.append(f"Auto-fix: line endings fixed ({crlf_fixed} files)")

    # 2. Detect correct Gradle version from project
    gradle_ver = None
    gradle_dist_url = None
    props_path = os.path.join(gdir, "gradle", "wrapper", "gradle-wrapper.properties")
    if os.path.exists(props_path):
        try:
            with open(props_path, "r") as f:
                for line in f:
                    if "distributionUrl" in line:
                        url = line.split("=", 1)[1].strip().replace("\\:", ":")
                        gradle_dist_url = url
                        # Extract version from URL like gradle-6.1.1-bin.zip
                        m = re.search(r"gradle-([0-9.]+)-", url)
                        if m:
                            gradle_ver = m.group(1)
                        break
        except Exception:
            pass
    if not gradle_ver:
        # Detect from AGP version in build.gradle
        for bg_name in ("build.gradle", "build.gradle.kts"):
            bg_path = os.path.join(gdir, bg_name)
            if os.path.exists(bg_path):
                try:
                    with open(bg_path, "r") as f:
                        content = f.read()
                    m = re.search(r"com\.android\.tools\.build:gradle:([0-9.]+)", content)
                    if m:
                        agp = m.group(1)
                        major = int(agp.split(".")[0])
                        minor = int(agp.split(".")[1]) if len(agp.split(".")) > 1 else 0
                        # AGP → compatible Gradle version mapping
                        if major <= 3:
                            gradle_ver = "6.1.1"
                        elif major == 4 and minor <= 0:
                            gradle_ver = "6.1.1"
                        elif major == 4 and minor <= 1:
                            gradle_ver = "6.7.1"
                        elif major == 4:
                            gradle_ver = "6.9.4"
                        elif major == 7 and minor <= 0:
                            gradle_ver = "7.0.2"
                        elif major == 7 and minor <= 2:
                            gradle_ver = "7.3.3"
                        elif major == 7 and minor <= 4:
                            gradle_ver = "7.5.1"
                        elif major == 8 and minor <= 1:
                            gradle_ver = "8.0"
                        elif major == 8 and minor <= 3:
                            gradle_ver = "8.4"
                        elif major == 8 and minor <= 5:
                            gradle_ver = "8.7"
                        else:
                            gradle_ver = "8.5"
                        logs.append(f"Auto-fix: detected AGP {agp} → Gradle {gradle_ver}")
                        break
                except Exception:
                    pass
    if not gradle_ver:
        gradle_ver = "8.5"  # Fallback default
    if not gradle_dist_url:
        gradle_dist_url = f"https://services.gradle.org/distributions/gradle-{gradle_ver}-bin.zip"

    # 3. Generate gradle wrapper if gradlew missing
    if not os.path.exists(gradlew):
        logs.append(f"Auto-fix: gradlew missing, downloading Gradle {gradle_ver}...")
        dl_dir = f"/tmp/gradle-inst/gradle-{gradle_ver}"
        if not os.path.exists(os.path.join(dl_dir, "bin", "gradle")):
            await run_cmd(
                f"curl -fsSL '{gradle_dist_url}' -o /tmp/gradle-dl.zip && "
                f"rm -rf /tmp/gradle-inst && "
                f"unzip -qo /tmp/gradle-dl.zip -d /tmp/gradle-inst",
                timeout=300,
            )
        gradle_bin = os.path.join(dl_dir, "bin", "gradle")
        if os.path.exists(gradle_bin):
            # Try to generate wrapper
            code, _, _ = await run_cmd(f"{gradle_bin} wrapper", cwd=gdir, timeout=180)
            if os.path.exists(gradlew):
                await run_cmd(f"chmod +x {gradlew}")
                logs.append(f"Auto-fix: gradle wrapper generated (v{gradle_ver})")
            else:
                logs.append(f"Auto-fix: wrapper generation failed, will use gradle binary directly")
        else:
            logs.append("Auto-fix: gradle download failed")

    # 3. Create local.properties if missing (sdk.dir)
    lp = os.path.join(gdir, "local.properties")
    if not os.path.exists(lp):
        ah = os.environ.get("ANDROID_HOME", "/usr/local/lib/android/sdk")
        with open(lp, "w") as f:
            f.write(f"sdk.dir={ah}\n")
        logs.append("Auto-fix: created local.properties (sdk.dir)")


async def build_native(project_dir, config):
    logs = []
    await setup_java(config.get("java_version", "11"))
    logs.append(f"Java {config.get('java_version','11')} ready")
    await setup_android_sdk(config.get("compile_sdk"), config.get("build_tools"))
    logs.append("Android SDK ready")

    await fix_common_issues(project_dir, logs)

    # Determine gradle command — gradlew preferred, fallback to downloaded gradle binary
    gradlew = os.path.join(project_dir, "gradlew")
    if os.path.exists(gradlew):
        await run_cmd(f"chmod +x {gradlew}")
        gcmd = "./gradlew"
    else:
        # Find any downloaded gradle binary in /tmp/gradle-inst/
        gcmd = None
        inst_dir = "/tmp/gradle-inst"
        if os.path.isdir(inst_dir):
            for entry in os.listdir(inst_dir):
                candidate = os.path.join(inst_dir, entry, "bin", "gradle")
                if os.path.exists(candidate):
                    gcmd = candidate
                    logs.append(f"Auto-fix: using {entry} binary (gradlew unavailable)")
                    break
        if not gcmd:
            return {"success": False, "error": "No gradlew found and could not install gradle.", "logs": logs}

    code, out, err = await run_cmd(f"{gcmd} assembleDebug --stacktrace", cwd=project_dir)
    logs.append(f"assembleDebug: {'OK' if code == 0 else 'FAIL'}")
    if code != 0:
        return {"success": False, "error": f"Debug build failed:\n{err[-3000:]}\n{out[-1000:]}", "logs": logs}

    code2, out2, err2 = await run_cmd(f"{gcmd} assembleRelease --stacktrace", cwd=project_dir)
    logs.append(f"assembleRelease: {'OK' if code2 == 0 else 'FAIL'}")

    code3, out3, err3 = await run_cmd(f"{gcmd} bundleRelease --stacktrace", cwd=project_dir)
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

    await fix_common_issues(project_dir, logs, "android")

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
