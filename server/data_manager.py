import aiohttp
import json
import base64
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class DataManager:
    def __init__(self, pat_token, private_repo):
        self.pat_token = pat_token
        self.private_repo = private_repo
        self.api_base = f"https://api.github.com/repos/{private_repo}/contents"
        self.headers = {
            "Authorization": f"token {pat_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "EarlStoreBot",
        }
        self._users = None
        self._stats = None

    async def _get_file(self, path):
        url = f"{self.api_base}/{path}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=self.headers) as r:
                    if r.status == 200:
                        data = await r.json()
                        content = base64.b64decode(data["content"]).decode()
                        return json.loads(content), data["sha"]
        except Exception as e:
            logger.error(f"_get_file {path}: {e}")
        return None, None

    async def _put_file(self, path, data, msg="update"):
        url = f"{self.api_base}/{path}"
        content_b64 = base64.b64encode(
            json.dumps(data, indent=2, ensure_ascii=False).encode()
        ).decode()
        _, sha = await self._get_file(path)
        payload = {"message": msg, "content": content_b64}
        if sha:
            payload["sha"] = sha
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(url, headers=self.headers, json=payload) as r:
                    return r.status in (200, 201)
        except Exception as e:
            logger.error(f"_put_file {path}: {e}")
        return False

    async def register_user(self, user_info):
        users, _ = await self._get_file("data/users.json")
        if users is None:
            users = {}
        uid = str(user_info["user_id"])
        if uid not in users:
            users[uid] = {**user_info, "joined_at": datetime.now().isoformat()}
            await self._put_file("data/users.json", users, "register user")
        self._users = users

    async def get_user_count(self):
        if self._users:
            return len(self._users)
        users, _ = await self._get_file("data/users.json")
        self._users = users or {}
        return len(self._users)

    async def get_all_users(self):
        if self._users:
            return self._users
        users, _ = await self._get_file("data/users.json")
        self._users = users or {}
        return self._users

    async def get_build_stats(self):
        if self._stats:
            return self._stats
        stats, _ = await self._get_file("data/build_stats.json")
        if not stats:
            stats = {
                "total_native": 0,
                "total_flutter": 0,
                "total_smali_native": 0,
                "total_smali_flutter": 0,
                "total_success": 0,
                "total_failed": 0,
                "recent_success": [],
            }
        self._stats = stats
        return stats

    async def add_build_history(self, info):
        """Only called for successful builds."""
        stats = await self.get_build_stats()
        type_key_map = {
            "native": "total_native",
            "flutter": "total_flutter",
            "smali_native": "total_smali_native",
            "smali_flutter": "total_smali_flutter",
            "smali": "total_smali_native",
        }
        key = type_key_map.get(info["project_type"], "total_native")
        stats[key] = stats.get(key, 0) + 1
        stats["total_success"] = stats.get("total_success", 0) + 1
        entry = {
            "username": info["username"],
            "project_name": info["project_name"],
            "project_type": info["project_type"],
            "time": datetime.now().strftime("%d/%m/%Y %H:%M"),
        }
        stats.setdefault("recent_success", []).insert(0, entry)
        stats["recent_success"] = stats["recent_success"][:5]
        await self._put_file("data/build_stats.json", stats, "update stats")
        self._stats = stats

    async def save_queue(self, data):
        await self._put_file("data/queue.json", data, "save queue")

    async def load_queue(self):
        data, _ = await self._get_file("data/queue.json")
        return data or {"current": None, "queue": []}
