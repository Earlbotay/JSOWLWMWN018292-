# 🏗 EARL STORE BUILD APK — Setup Guide

## Arsitektur

```
┌─────────────────────────────────────────────────────┐
│  PUBLIC REPO (unlimited minutes)                    │
│  ├── server/          ← Bot code                    │
│  ├── .github/workflows/server.yml  ← Self-respawn   │
│  └── scripts/         ← Setup helpers               │
└────────────────────┬────────────────────────────────┘
                     │ GitHub API (PAT_TOKEN)
┌────────────────────▼────────────────────────────────┐
│  PRIVATE REPO (data storage)                        │
│  └── data/                                          │
│      ├── users.json        ← Semua user terdaftar   │
│      ├── build_stats.json  ← Stats & sejarah build  │
│      └── queue.json        ← Queue persistence      │
└─────────────────────────────────────────────────────┘
```

---

## Step 1: Buat 2 Repo

### Public Repo
1. Buat repo baru di GitHub (public)
2. Push semua file dari folder `earl-store-build/` ke repo ini
3. Bila push ke `main`, workflow auto-start! 🚀

### Private Repo
1. Buat repo baru di GitHub (private)
2. Push file dari `earl-store-private-repo.zip`:
   ```
   data/users.json        → isi: {}
   data/build_stats.json  → isi: {"total_native":0,"total_flutter":0,"total_success":0,"total_failed":0,"recent_success":[]}
   data/queue.json        → isi: {"current":null,"queue":[]}
   ```

---

## Step 2: Buat Telegram Bot

1. Chat [@BotFather](https://t.me/BotFather) di Telegram
2. Send `/newbot`
3. Beri nama: `EARL STORE BUILD APK`
4. Simpan **BOT TOKEN** yang diberi

---

## Step 3: Dapatkan Owner Telegram ID

1. Chat [@userinfobot](https://t.me/userinfobot) di Telegram
2. Send `/start`
3. Simpan **ID** kau (nombor)

---

## Step 4: Setup Channel/Group (Optional)

Kalau nak force join:
1. Buat channel/group Telegram
2. Add bot sebagai admin
3. Dapatkan channel ID (contoh: `-1001234567890`)
4. Dapatkan invite link (contoh: `https://t.me/+xxxxx`)

Kalau tak nak force join, biarkan `CHANNEL_ID` kosong.

---

## Step 5: Buat GitHub PAT Token

1. Pergi ke GitHub → Settings → Developer Settings → Personal Access Tokens → Fine-grained tokens
2. Buat token baru dengan permissions:
   - **Actions**: Read & Write (untuk respawn workflow)
   - **Contents**: Read & Write (untuk read/write data di private repo)
3. Scope: Kedua-dua repo (public + private)
4. Simpan token

---

## Step 6: Upload Video & Dapatkan Link

1. Upload video ke [Catbox](https://catbox.moe/) atau mana-mana file hosting
2. Salin direct link video tu (contoh: `https://files.catbox.moe/abc123.mp4`)
3. Link ni akan diset sebagai secret `VIDEO_URL`

---

## Step 7: Set GitHub Secrets

Pergi ke **Public Repo** → Settings → Secrets and variables → Actions → New repository secret

| Secret Name    | Value                                          |
|---------------|------------------------------------------------|
| `BOT_TOKEN`    | Token dari BotFather                          |
| `PAT_TOKEN`    | GitHub PAT token dari Step 5                  |
| `OWNER_TG_ID`  | Telegram ID kau (nombor)                      |
| `CHANNEL_ID`   | Channel ID (kosongkan jika tak perlu)         |
| `CHANNEL_LINK` | Channel invite link (kosongkan jika tak perlu)|
| `PRIVATE_REPO` | `username/nama-repo-private`                  |
| `VIDEO_URL`    | Direct link video dari Step 6                 |

> ⚠️ **Tak perlu set `PUBLIC_REPO`** — GitHub Actions auto-detect repo sendiri via `GITHUB_REPOSITORY`.

---

## Step 8: Start Bot!

**Auto-start:** Push code ke `main` branch → workflow auto-trigger! 🚀

**Manual start (kalau perlu):**
1. Pergi ke Public Repo → Actions tab
2. Klik workflow "Server" di sidebar
3. Klik "Run workflow" → "Run workflow"

Bot akan auto-respawn setiap ~5 jam, berjalan 24/7.

---

## Cara Guna Bot

1. `/start` → Papar video + info bot + butang menu
2. Hantar file `.zip` (project Android Native atau Flutter)
3. Reply file `.zip` tu dengan `/build`
4. Tunggu bot compile (~5-20 minit)
5. Bot hantar hasil build (APK debug + release + AAB)

### Owner Commands
- `/forward` → Reply mesej, bot forward ke semua user

---

## Features

| Feature | Keterangan |
|---------|-----------|
| 🏗 Building APK | Status compile, total Native/Flutter, 5 sejarah berjaya |
| ⏳ Queue | Berapa user dalam barisan |
| 📖 Panduan | Cara guna bot |
| 👥 Total User | Jumlah pengguna |
| 👑 Owner | Link terus ke @earlxz |
| ⏱ Countdown | Masa sebelum server restart |
| 🔄 Auto-respawn | Server 24/7 (5 jam cycle) |
| 📱 Auto-detect | Native Android & Flutter |
| 📦 GoFile | Upload kalau file > 50MB |
| 📢 Force join | Optional channel/group join |
| 📣 Broadcast | /forward untuk owner |

---

## Troubleshooting

- **Bot tak reply**: Check Actions tab, pastikan workflow running
- **Video tak keluar**: Pastikan `VIDEO_URL` secret betul (direct link)
- **Build gagal**: Semak error log yang bot hantar
- **Queue hilang**: Queue auto-persist ke private repo sebelum respawn
- **Respawn gagal**: Check PAT_TOKEN masih valid & ada Actions permission
