import asyncio
import os
import re
import shutil
import logging
import zipfile
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


def _ver_tuple(v):
    """Convert version string to tuple for comparison."""
    return tuple(int(x) for x in v.split("."))


async def fix_flutter_versions(project_dir, logs):
    """Auto-fix Gradle/AGP versions in android/ to meet Flutter minimums.
    Works like Native: detect AGP, map to correct Gradle, upgrade if needed."""
    android_dir = os.path.join(project_dir, "android")
    if not os.path.isdir(android_dir):
        return

    min_agp = "8.1.1"    # Flutter minimum AGP
    min_gradle = "8.7"    # Flutter minimum Gradle

    # AGP -> minimum compatible Gradle version mapping (same logic as Native)
    def agp_to_gradle(agp_ver):
        parts = agp_ver.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        if major == 8 and minor <= 1:
            return "8.0"
        elif major == 8 and minor <= 3:
            return "8.4"
        elif major == 8 and minor <= 5:
            return "8.7"
        elif major == 8 and minor <= 7:
            return "8.9"
        elif major >= 9:
            return "8.11.1"
        else:
            return min_gradle  # Old AGP will be upgraded, use Flutter min

    # -- Step 1: Detect current AGP version --
    cur_agp = None
    agp_file = None
    agp_start = None
    agp_end = None

    # Check settings.gradle first (newer plugin style)
    for sg_name in ("settings.gradle", "settings.gradle.kts"):
        sg_path = os.path.join(android_dir, sg_name)
        if os.path.exists(sg_path):
            try:
                with open(sg_path, "r") as f:
                    sg_content = f.read()
                pat = r'(id\s+["\x27]com\.android\.application["\x27]\s+version\s+["\x27])([0-9.]+)(["\x27])'
                m = re.search(pat, sg_content)
                if m:
                    cur_agp = m.group(2)
                    agp_file = sg_path
                    agp_start = m.start(2)
                    agp_end = m.end(2)
                    break
            except Exception:
                pass

    # Fallback: build.gradle (older classpath style)
    if not cur_agp:
        for bg_name in ("build.gradle", "build.gradle.kts"):
            bg_path = os.path.join(android_dir, bg_name)
            if os.path.exists(bg_path):
                try:
                    with open(bg_path, "r") as f:
                        bg_content = f.read()
                    m = re.search(r"(com\.android\.tools\.build:gradle:)([0-9.]+)", bg_content)
                    if m:
                        cur_agp = m.group(2)
                        agp_file = bg_path
                        agp_start = m.start(2)
                        agp_end = m.end(2)
                        break
                except Exception:
                    pass

    # -- Step 2: Upgrade AGP if below Flutter minimum --
    final_agp = cur_agp or min_agp
    if cur_agp and _ver_tuple(cur_agp) < _ver_tuple(min_agp):
        final_agp = min_agp
        if agp_file and agp_start is not None:
            try:
                with open(agp_file, "r") as f:
                    fc = f.read()
                new_fc = fc[:agp_start] + min_agp + fc[agp_end:]
                with open(agp_file, "w") as f:
                    f.write(new_fc)
                logs.append(f"Auto-fix: AGP {cur_agp} -> {min_agp} ({os.path.basename(agp_file)})")
            except Exception:
                pass

    # -- Step 3: Determine correct Gradle version from AGP --
    target_gradle = agp_to_gradle(final_agp)
    # Floor at Flutter minimum Gradle
    if _ver_tuple(target_gradle) < _ver_tuple(min_gradle):
        target_gradle = min_gradle

    # -- Step 4: Update Gradle version in gradle-wrapper.properties --
    props_path = os.path.join(android_dir, "gradle", "wrapper", "gradle-wrapper.properties")
    if os.path.exists(props_path):
        try:
            with open(props_path, "r") as f:
                pc = f.read()
            m = re.search(r"gradle-([0-9.]+)-", pc)
            if m:
                cur_gradle = m.group(1)
                if _ver_tuple(cur_gradle) < _ver_tuple(target_gradle):
                    new_url = f"https\\://services.gradle.org/distributions/gradle-{target_gradle}-bin.zip"
                    new_pc = re.sub(r"distributionUrl=.*", f"distributionUrl={new_url}", pc)
                    with open(props_path, "w") as f:
                        f.write(new_pc)
                    logs.append(f"Auto-fix: Gradle {cur_gradle} -> {target_gradle}")
        except Exception:
            pass
    else:
        # Create gradle-wrapper.properties if missing
        try:
            os.makedirs(os.path.join(android_dir, "gradle", "wrapper"), exist_ok=True)
            with open(props_path, "w") as f:
                f.write(
                    "distributionBase=GRADLE_USER_HOME\n"
                    "distributionPath=wrapper/dists\n"
                    f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{target_gradle}-bin.zip\n"
                    "zipStoreBase=GRADLE_USER_HOME\n"
                    "zipStorePath=wrapper/dists\n"
                )
            logs.append(f"Auto-fix: created gradle-wrapper.properties (Gradle {target_gradle})")
        except Exception:
            pass


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
        return {"success": False, "error": f"Debug build failed\n{err}\n{out}", "logs": logs}

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
        return {"success": False, "error": f"No output files found.
{err}
{err2}
{err3}", "logs": logs}
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
    await fix_flutter_versions(project_dir, logs)

    gw = os.path.join(project_dir, "android", "gradlew")
    if os.path.exists(gw):
        await run_cmd(f"chmod +x {gw}")

    code, out, err = await run_cmd("flutter pub get", cwd=project_dir, timeout=300)
    logs.append(f"pub get: {'OK' if code == 0 else 'FAIL'}")
    if code != 0:
        return {"success": False, "error": f"flutter pub get failed\n{err}\n{out}", "logs": logs}

    code, out, err = await run_cmd("flutter build apk --debug", cwd=project_dir)
    logs.append(f"apk debug: {'OK' if code == 0 else 'FAIL'}")
    if code != 0:
        return {"success": False, "error": f"Debug build failed\n{err}\n{out}", "logs": logs}

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
        return {"success": False, "error": f"No output files found.
{err}
{err2}
{err3}", "logs": logs}
    return {"success": True, "files": files, "logs": logs}


def _find_zipalign():
    """Find zipalign binary in Android SDK build-tools."""
    if shutil.which("zipalign"):
        return "zipalign"
    ah = os.environ.get("ANDROID_HOME", "/usr/local/lib/android/sdk")
    bt_dir = os.path.join(ah, "build-tools")
    if os.path.isdir(bt_dir):
        for ver in sorted(os.listdir(bt_dir), reverse=True):
            za = os.path.join(bt_dir, ver, "zipalign")
            if os.path.exists(za):
                return za
    return None


def _fix_apktool_donotcompress(project_dir):
    """Ensure native .so files won't be compressed in rebuilt APK.
    If extractNativeLibs=false (modern APKs), .so must be stored
    uncompressed and page-aligned or the APK won't install."""
    yml_path = os.path.join(project_dir, "apktool.yml")
    if not os.path.exists(yml_path):
        return False
    lib_dir = os.path.join(project_dir, "lib")
    if not os.path.isdir(lib_dir):
        return False
    # Check if any .so files exist
    has_so = any(
        f.endswith(".so")
        for _, _, files in os.walk(lib_dir)
        for f in files
    )
    if not has_so:
        return False
    with open(yml_path, "r") as f:
        content = f.read()
    # Already has .so in doNotCompress
    if re.search(r'-\s*["\']?\.?so["\']?\s*$', content, re.MULTILINE):
        return False
    # Add .so to doNotCompress list
    if "doNotCompress:" in content:
        content = re.sub(r'(doNotCompress:\n)', r'\1- .so\n', content)
    else:
        content += "\ndoNotCompress:\n- .so\n"
    with open(yml_path, "w") as f:
        f.write(content)
    return True


def _find_apksigner():
    """Find apksigner in Android SDK build-tools."""
    ah = os.environ.get("ANDROID_HOME", "/usr/local/lib/android/sdk")
    bt_dir = os.path.join(ah, "build-tools")
    if os.path.isdir(bt_dir):
        for ver in sorted(os.listdir(bt_dir), reverse=True):
            path = os.path.join(bt_dir, ver, "apksigner")
            if os.path.exists(path):
                return path
    return None


async def _ensure_debug_keystore():
    """Generate a debug keystore for signing. Returns path or None."""
    ks_path = "/tmp/debug-sign.jks"
    if os.path.exists(ks_path):
        return ks_path
    code, _, _ = await run_cmd(
        'keytool -genkeypair -v -keystore ' + ks_path +
        ' -alias debug -keyalg RSA -keysize 2048 -validity 10000'
        ' -storepass android -keypass android'
        ' -dname "CN=Debug,O=Debug,C=US"',
        timeout=30,
    )
    return ks_path if code == 0 and os.path.exists(ks_path) else None


async def _sign_apk(apk_path, keystore, apksigner_bin, logs):
    """Sign a zipaligned APK using apksigner."""
    code, _, err = await run_cmd(
        f'"{apksigner_bin}" sign --ks "{keystore}" --ks-key-alias debug'
        f' --ks-pass pass:android --key-pass pass:android "{apk_path}"',
        timeout=120,
    )
    if code == 0:
        logs.append(f"Signed: {os.path.basename(apk_path)}")
        return True
    logs.append(f"Sign FAIL: {os.path.basename(apk_path)} — {(err or '')[:200]}")
    return False


def _strip_apk_signatures(apk_path):
    """Remove META-INF/ (signatures) from an APK so it can be re-signed."""
    tmp_path = apk_path + '.unsigned'
    try:
        with zipfile.ZipFile(apk_path, 'r') as zin:
            with zipfile.ZipFile(tmp_path, 'w') as zout:
                for item in zin.infolist():
                    if not item.filename.upper().startswith('META-INF/'):
                        zout.writestr(item, zin.read(item.filename))
        os.replace(tmp_path, apk_path)
        return True
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False


def _find_splits_dir(project_dir):
    """Find directory containing split APK files.
    Searches: project_dir children, then parent dir children.
    Matches any folder containing split_*.apk files, regardless of folder name."""
    search_roots = [project_dir, os.path.dirname(project_dir)]
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            candidate = os.path.join(root, name)
            if not os.path.isdir(candidate):
                continue
            has_split_apks = any(
                f.lower().startswith("split_") and f.lower().endswith(".apk")
                for f in os.listdir(candidate)
            )
            if has_split_apks:
                return candidate
    return None


async def _package_as_apks(base_apk, splits_dir, output_path, zipalign_bin, apksigner_bin, keystore, logs):
    """Package rebuilt base.apk + original split APKs into .apks (ZIP) format.
    Strips original signatures, zipaligns, signs with debug key, then packages."""
    split_apks = sorted([
        os.path.join(splits_dir, f)
        for f in os.listdir(splits_dir)
        if f.lower().endswith('.apk')
    ])
    if not split_apks:
        logs.append("splits/: no APK files found, skipping APKS packaging")
        return None

    # Strip existing signatures from split APKs
    stripped = 0
    for sa in split_apks:
        if _strip_apk_signatures(sa):
            stripped += 1
    if stripped:
        logs.append(f"Stripped signatures from {stripped} split APK(s)")

    # Zipalign splits if possible
    if zipalign_bin:
        for sa in split_apks:
            aligned = sa + '.aligned'
            code, _, _ = await run_cmd(
                f'"{zipalign_bin}" -p -f 4 "{sa}" "{aligned}"',
                timeout=60,
            )
            if code == 0 and os.path.exists(aligned):
                os.replace(aligned, sa)

    # Sign splits with same debug key as base.apk
    if apksigner_bin and keystore:
        for sa in split_apks:
            await _sign_apk(sa, keystore, apksigner_bin, logs)

    # Create .apks file (ZIP with STORED compression — APKs are already compressed)
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_STORED) as zf:
        zf.write(base_apk, 'base.apk')
        for sa in split_apks:
            zf.write(sa, os.path.basename(sa))

    logs.append(f"APKS packaged: base.apk + {len(split_apks)} split(s)")
    return output_path


async def build_smali(project_dir, config):
    logs = []
    await setup_java(config.get("java_version", "17"))
    logs.append(f"Java {config.get('java_version', '17')} ready")

    # Pre-build: ensure .so in doNotCompress (fixes extractNativeLibs=false)
    if _fix_apktool_donotcompress(project_dir):
        logs.append("Auto-fix: added .so to doNotCompress (apktool.yml)")

    # Build with apktool (try --use-aapt2 first)
    code, out, err = await run_cmd("apktool b . --use-aapt2", cwd=project_dir)
    logs.append(f"apktool build (aapt2): {'OK' if code == 0 else 'FAIL'}")

    if code != 0:
        # Retry without --use-aapt2 (older decompiled projects)
        code, out, err = await run_cmd("apktool b .", cwd=project_dir)
        logs.append(f"apktool build (aapt1 fallback): {'OK' if code == 0 else 'FAIL'}")

    if code != 0:
        return {"success": False, "error": f"Smali build failed\n{err}\n{out}", "logs": logs}

    # Find output APK in dist/
    files = []
    dist_dir = os.path.join(project_dir, "dist")
    if os.path.exists(dist_dir):
        for fn in os.listdir(dist_dir):
            if fn.endswith(".apk"):
                files.append(os.path.join(dist_dir, fn))

    if not files:
        return {"success": False, "error": f"No output APK found in dist/\n{err}", "logs": logs}

    # Post-build: zipalign APKs (page-align native libs for compatibility)
    zipalign = _find_zipalign()
    if zipalign:
        for i, apk_path in enumerate(files):
            aligned_path = apk_path + ".aligned"
            zcode, _, zerr = await run_cmd(
                f'"{zipalign}" -p -f 4 "{apk_path}" "{aligned_path}"',
                timeout=120,
            )
            if zcode == 0 and os.path.exists(aligned_path):
                os.replace(aligned_path, apk_path)
                logs.append(f"zipalign: OK ({os.path.basename(apk_path)})")
            else:
                logs.append(f"zipalign: FAIL ({zerr[:200] if zerr else 'unknown'})")
    else:
        logs.append("zipalign: not found (skipped)")

    # Sign APK(s) with debug key
    apksigner = _find_apksigner()
    keystore = await _ensure_debug_keystore()
    signed = False
    if apksigner and keystore:
        sign_results = []
        for p in files:
            sign_results.append(await _sign_apk(p, keystore, apksigner, logs))
        signed = all(sign_results)
    else:
        if not apksigner:
            logs.append("apksigner: not found (signing skipped)")
        if not keystore:
            logs.append("keystore: generation failed (signing skipped)")

    # Check for split APK directory → package as APKS
    # Flexible detection: search for any folder containing split_*.apk files
    splits_dir = _find_splits_dir(project_dir)
    if splits_dir:
        logs.append(f"Found split APKs in: {os.path.basename(splits_dir)}/")
        apks_name = os.path.splitext(os.path.basename(files[0]))[0] + ".apks"
        apks_path = os.path.join(os.path.dirname(files[0]), apks_name)
        packaged = await _package_as_apks(files[0], splits_dir, apks_path, zipalign, apksigner, keystore, logs)
        if packaged:
            return {"success": True, "files": [packaged], "logs": logs, "output_format": "apks", "signed": signed}

    return {"success": True, "files": files, "logs": logs, "signed": signed}


async def build_project(project_dir, project_info):
    t = project_info["type"]
    c = project_info["config"]
    if t == "native":
        return await build_native(project_dir, c)
    if t == "flutter":
        return await build_flutter(project_dir, c)
    if t == "smali":
        return await build_smali(project_dir, c)
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
