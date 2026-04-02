# EARL STORE — BUILD APK: Setup Guide

## Prerequisites

- GitHub account with Actions enabled
- Telegram Bot (from @BotFather)
- Private GitHub repo for data storage

## Step 1: Create Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the **BOT_TOKEN**

## Step 2: Create Private Data Repo

1. Create a **private** GitHub repository (e.g., `myname/bot-data`)
2. Create a `data/` folder with empty JSON files:
   - `data/users.json` → `{}`
   - `data/build_stats.json` → `{}`
   - `data/queue.json` → `{}`

## Step 3: Generate PAT Token

1. Go to GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Create token with **repo** access to your private data repo
3. Copy the token

## Step 4: Get Your Telegram User ID

1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. Copy your numeric user ID

## Step 5: Setup GitHub Secrets

Go to your **bot repo** → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Required | Description |
|--------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram bot token from BotFather |
| `PAT_TOKEN` | ✅ | GitHub PAT for private data repo |
| `PRIVATE_REPO` | ✅ | `owner/repo-name` of private data repo |
| `OWNER_TG_ID` | ✅ | Your Telegram user ID (for /foward) |
| `CHANNEL_ID` | ❌ | Channel ID for join-check & notifications |
| `CHANNEL_LINK` | ❌ | Channel invite link |
| `VIDEO_URL` | ❌ | Direct URL to intro video |
| `API_ID` | ❌ | Telegram API ID (for Local API Server) |
| `API_HASH` | ❌ | Telegram API Hash (for Local API Server) |

## Step 6: Local API Server (Optional — Removes File Size Limit)

To remove Telegram's file size limits:

1. Go to [my.telegram.org](https://my.telegram.org) → API development tools
2. Create an app and copy **API_ID** and **API_HASH**
3. Add them as GitHub Secrets

When `API_ID` and `API_HASH` are set:
- Bot automatically uses Local API Server
- **Download**: Unlimited (no 20MB cap)
- **Upload**: Up to 2GB (no 50MB cap)
- No other config needed — `USE_LOCAL_API` and `LOCAL_API_URL` are auto-detected

## Step 7: Deploy

1. Push all files to your bot repo's `main` branch
2. The workflow starts automatically
3. Bot runs for ~5 hours, then auto-restarts

## Supported Project Types

| Level | Type | Detection | Build Tool |
|-------|------|-----------|------------|
| **Source Code** | Android Native | `build.gradle` / `build.gradle.kts` | Gradle (`assembleDebug/Release`) |
| **Source Code** | Flutter | `pubspec.yaml` | Flutter CLI (`flutter build apk`) |
| **Smali** | Native (apktool) | `apktool.yml` | apktool (`apktool b`) |
| **Smali** | Flutter (apktool) | `apktool.yml` + `flutter_assets` | apktool (`apktool b`) |

## Features

- ✅ Auto-detect project type (Native / Flutter / Smali)
- ✅ Smali sub-type detection (originally Native or Flutter)
- ✅ Build queue system
- ✅ Auto-restart with queue persistence
- ✅ Channel notifications on successful build
- ✅ Owner broadcast (/foward)
- ✅ Local API Server (optional — no file size limit)
- ✅ Cloudflare Tunnel download portal (for >2GB output)
- ✅ GoFile fallback for large files
- ✅ Per-user Code ID for download portal access

## Download Portal (>2GB Output)

When build output exceeds 2GB:
1. File is uploaded to **GoFile** (direct download link)
2. File is also stored temporarily on the server
3. User receives a **Cloudflare Tunnel link** + their **Code ID**
4. User can visit the portal, enter their code, and download
5. Portal link changes on every restart (temporary URL)
6. Files expire when the bot restarts

## Troubleshooting

- **"Unsupported project"**: Ensure zip contains `build.gradle`, `pubspec.yaml`, or `apktool.yml`
- **Build timeout**: Builds have a 20-minute timeout
- **Java errors**: Bot auto-detects Java version from AGP. Supports Java 8, 11, 17
- **Gradle errors**: Bot auto-fixes missing wrapper, CRLF line endings, and missing local.properties
- **Flutter version errors**: Bot auto-upgrades AGP/Gradle to meet Flutter minimums
- **Smali build errors**: Tries `--use-aapt2` first, falls back to aapt1
- **File too large**: Set up Local API Server (Step 6) to increase limits
- **Tunnel not working**: cloudflared is installed automatically. Check workflow logs
