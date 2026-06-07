"""
Apify API 客户端 — TikTok & Instagram 达人搜索
"""

import json
import urllib.request
import hashlib
import sqlite3
import os
import time

CACHE_DB = os.path.join(os.path.dirname(__file__), "apify_cache.db")
CACHE_TTL = 86400  # 24小时


def _get_cache_db():
    conn = sqlite3.connect(CACHE_DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS apify_cache (
            cache_key TEXT PRIMARY KEY,
            data_json TEXT,
            created_at REAL
        )
    """)
    conn.commit()
    return conn


def _cache_key(platform, keywords):
    raw = f"{platform}:{','.join(sorted(keywords))}"
    return hashlib.md5(raw.encode()).hexdigest()


class ApifyClient:
    def __init__(self, api_token):
        self.token = api_token
        self.base_url = "https://api.apify.com/v2"

    # ──────────────── 底层调用 ────────────────

    def _run_sync(self, actor_id, input_data, timeout=120):
        """同步运行 Actor 并获取数据集"""
        url = f"{self.base_url}/acts/{actor_id}/run-sync-get-dataset-items?token={self.token}&format=json"
        payload = json.dumps(input_data).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    # ──────────────── 缓存层 ────────────────

    def _cache_get(self, platform, keywords):
        conn = _get_cache_db()
        c = conn.cursor()
        key = _cache_key(platform, keywords)
        c.execute("SELECT data_json, created_at FROM apify_cache WHERE cache_key=?", (key,))
        row = c.fetchone()
        conn.close()
        if row:
            age = time.time() - row[1]
            if age < CACHE_TTL:
                return json.loads(row[0])
        return None

    def _cache_set(self, platform, keywords, data):
        conn = _get_cache_db()
        c = conn.cursor()
        key = _cache_key(platform, keywords)
        c.execute(
            "INSERT OR REPLACE INTO apify_cache (cache_key, data_json, created_at) VALUES (?,?,?)",
            (key, json.dumps(data, ensure_ascii=False), time.time())
        )
        conn.commit()
        conn.close()

    # ──────────────── TikTok ────────────────

    def search_tiktok_profiles(self, keywords, limit=20):
        """
        通过 hashtag 搜索 TikTok 内容，提取创作者信息
        keywords: ["假发", "wig", "beauty"] → hashtags: ["wig", "beauty"]
        """
        cached = self._cache_get("tiktok", keywords)
        if cached:
            print(f"[Apify] TikTok 缓存命中 ({len(cached)}条)")
            return cached

        # 取英文关键词作为 hashtag
        hashtags = []
        for kw in keywords[:3]:
            tag = kw.lower().replace(" ", "").replace("#", "")
            if tag:
                hashtags.append(tag)

        if not hashtags:
            hashtags = ["viral"]  # fallback

        input_data = {
            "hashtagConfig": {
                "hashtags": hashtags,
                "resultsPerPage": min(limit * 2, 30)
            }
        }

        try:
            results = self._run_sync(
                "scraping_solutions~tiktok-scraper",
                input_data,
                timeout=180
            )

            # 从帖子中提取唯一创作者
            creators = {}
            for post in results:
                author = post.get("authorMeta", {})
                author_id = author.get("id")
                if author_id and author_id not in creators:
                    creators[author_id] = {
                        "platform": "tiktok",
                        "author": author,
                        "posts": []
                    }
                if author_id and len(creators[author_id]["posts"]) < 10:
                    creators[author_id]["posts"].append(post)

            profiles = list(creators.values())[:limit]
            self._cache_set("tiktok", keywords, profiles)
            print(f"[Apify] TikTok 搜索完成 ({len(profiles)}位创作者)")
            return profiles

        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            print(f"[Apify] TikTok HTTP {e.code}: {body[:200]}")
            return []
        except Exception as e:
            print(f"[Apify] TikTok 搜索失败: {e}")
            return []

    # ──────────────── Instagram ────────────────

    def search_instagram_profiles(self, keywords, limit=20):
        """
        通过关键词搜索 Instagram 用户
        keywords: ["beauty", "makeup", "skincare"] → search: "beauty makeup skincare"
        """
        cached = self._cache_get("instagram", keywords)
        if cached:
            print(f"[Apify] Instagram 缓存命中 ({len(cached)}条)")
            return cached

        search_query = " ".join(keywords[:8]) if keywords else "influencer"

        # 使用更大的搜索限制（上限50），同时尝试两种搜索方式
        input_data = {
            "search": search_query,
            "searchType": "user",
            "searchLimit": min(limit * 2, 50),
            "resultsType": "details"
        }

        try:
            results = self._run_sync(
                "apify~instagram-scraper",
                input_data,
                timeout=180
            )

            profiles = [{"platform": "instagram", "profile": r} for r in results[:limit]]
            self._cache_set("instagram", keywords, profiles)
            print(f"[Apify] Instagram 搜索完成 ({len(profiles)}个用户)")
            return profiles

        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            print(f"[Apify] Instagram HTTP {e.code}: {body[:200]}")
            return []
        except Exception as e:
            print(f"[Apify] Instagram 搜索失败: {e}")
            return []
