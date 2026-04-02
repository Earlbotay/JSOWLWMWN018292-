import os
import re
import json
import zipfile
import logging

logger = logging.getLogger(__name__)


def extract_zip(zip_path, dest):
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)
    items = [d for d in os.listdir(dest) if os.path.isdir(os.path.join(dest, d))]
    if len(items) == 1:
        nested = os.path.join(dest, items[0])
        markers = (
            "pubspec.yaml", "build.gradle", "build.gradle.kts",
            "settings.gradle", "settings.gradle.kts", "app",
            "apktool.yml",
        )
        if any(os.path.exists(os.path.join(nested, m)) for m in markers):
            return nested
    return dest


def _detect_type(project_dir):
    if os.path.exists(os.path.join(project_dir, "pubspec.yaml")):
        return "flutter"
    for name in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"):
        if os.path.exists(os.path.join(project_dir, name)):
            return "native"
    if os.path.exists(os.path.join(project_dir, "apktool.yml")):
        return "smali"
    return None


def _read(path):
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""


def _gradle_ver(project_dir, sub=""):
    props = os.path.join(project_dir, sub, "gradle", "wrapper", "gradle-wrapper.properties")
    m = re.search(r"gradle-(\d+\.\d+(?:\.\d+)?)", _read(props))
    return m.group(1) if m else None


def _agp_ver(content):
    for p in [
        r"com\.android\.tools\.build:gradle:(\d+\.\d+\.\d+)",
        r'id\s*\(?\s*["\x27]com\.android\.\w+["\x27]\s*\)?\s*version\s*["\x27](\d+\.\d+\.\d+)',
    ]:
        m = re.search(p, content)
        if m:
            return m.group(1)
    return None


def _java_for_agp(agp):
    if not agp:
        return "17"
    major = int(agp.split(".")[0])
    if major >= 8:
        return "17"
    if major >= 7:
        return "11"
    return "8"


def _detect_native(project_dir):
    cfg = {
        "gradle_version": _gradle_ver(project_dir),
        "agp_version": None,
        "compile_sdk": None, "min_sdk": None,
        "target_sdk": None, "build_tools": None,
        "java_version": "11",
    }
    for gf in ("build.gradle", "build.gradle.kts"):
        content = _read(os.path.join(project_dir, gf))
        if content:
            v = _agp_ver(content)
            if v:
                cfg["agp_version"] = v
    for gf in ("app/build.gradle", "app/build.gradle.kts"):
        content = _read(os.path.join(project_dir, gf))
        if content:
            for key, pat in [
                ("compile_sdk", r"compileSdk(?:Version)?\s*[=:]?\s*(\d+)"),
                ("min_sdk", r"minSdk(?:Version)?\s*[=:]?\s*(\d+)"),
                ("target_sdk", r"targetSdk(?:Version)?\s*[=:]?\s*(\d+)"),
                ("build_tools", r'buildToolsVersion\s*[=:]?\s*["\x27]([^"\x27]+)'),
            ]:
                m = re.search(pat, content)
                if m:
                    cfg[key] = m.group(1)
    cfg["java_version"] = _java_for_agp(cfg["agp_version"])
    return cfg


def _detect_flutter(project_dir):
    cfg = {
        "flutter_version": None,
        "gradle_version": _gradle_ver(project_dir, "android"),
        "agp_version": None,
        "java_version": "17",
    }
    fv = os.path.join(project_dir, ".flutter-version")
    if os.path.exists(fv):
        cfg["flutter_version"] = _read(fv).strip()
    fvm = os.path.join(project_dir, ".fvm", "fvm_config.json")
    if os.path.exists(fvm):
        try:
            cfg["flutter_version"] = json.loads(_read(fvm)).get("flutterSdkVersion")
        except Exception:
            pass
    if not cfg["flutter_version"]:
        cfg["flutter_version"] = "stable"
    for gf in ("android/build.gradle", "android/build.gradle.kts"):
        content = _read(os.path.join(project_dir, gf))
        v = _agp_ver(content)
        if v:
            cfg["agp_version"] = v
    cfg["java_version"] = _java_for_agp(cfg["agp_version"])
    return cfg


def _detect_smali(project_dir):
    cfg = {
        "java_version": "17",
        "min_sdk": None,
        "target_sdk": None,
        "sub_type": "native",
    }
    apktool_yml = os.path.join(project_dir, "apktool.yml")
    if os.path.exists(apktool_yml):
        try:
            with open(apktool_yml, "r") as f:
                content = f.read()
            m = re.search(r"minSdkVersion:\s*'?(\d+)'?", content)
            if m:
                cfg["min_sdk"] = m.group(1)
            m = re.search(r"targetSdkVersion:\s*'?(\d+)'?", content)
            if m:
                cfg["target_sdk"] = m.group(1)
        except Exception:
            pass

    # Detect if originally Flutter or Native
    flutter_assets = os.path.join(project_dir, "assets", "flutter_assets")
    if os.path.exists(flutter_assets):
        cfg["sub_type"] = "flutter"
    else:
        lib_dir = os.path.join(project_dir, "lib")
        if os.path.isdir(lib_dir):
            for root, dirs, files in os.walk(lib_dir):
                if "libflutter.so" in files:
                    cfg["sub_type"] = "flutter"
                    break

    return cfg


def detect_project(project_dir):
    t = _detect_type(project_dir)
    if t == "flutter":
        return {"type": "flutter", "config": _detect_flutter(project_dir)}
    if t == "native":
        return {"type": "native", "config": _detect_native(project_dir)}
    if t == "smali":
        return {"type": "smali", "config": _detect_smali(project_dir)}
    return None
