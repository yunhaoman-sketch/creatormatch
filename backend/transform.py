"""
Apify 原始数据 → 项目统一 Influencer 格式转换
"""

from data import NICHE_KEYWORDS


def _format_number(n):
    """数字展示格式化: 128000 → '128k'"""
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _infer_niche(tags, bio, posts_text):
    """从标签/简介/帖子内容推断领域"""
    text = (bio or "") + " " + " ".join(tags or []) + " " + (posts_text or "")
    text_lower = text.lower()

    best_niche = "lifestyle"
    best_score = 0

    for niche, keywords in NICHE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        if score > best_score:
            best_score = score
            best_niche = niche

    return best_niche


def _infer_country(region_text):
    """从地区文本推断国家代码"""
    region_lower = (region_text or "").lower()
    country_map = {
        "us": "US", "usa": "US", "united states": "US", "america": "US",
        "uk": "UK", "united kingdom": "UK", "britain": "UK", "england": "UK",
        "ca": "CA", "canada": "CA",
        "au": "AU", "australia": "AU",
        "fr": "FR", "france": "FR",
        "de": "DE", "germany": "DE",
        "jp": "JP", "japan": "JP",
        "kr": "KR", "korea": "KR", "south korea": "KR",
        "sg": "SG", "singapore": "SG",
        "th": "TH", "thailand": "TH",
        "ae": "AE", "uae": "AE", "dubai": "AE",
        "es": "ES", "spain": "ES",
        "nl": "NL", "netherlands": "NL",
        "se": "SE", "sweden": "SE",
        "br": "BR", "brazil": "BR",
        "mx": "MX", "mexico": "MX",
        "id": "ID", "indonesia": "ID",
        "in": "IN", "india": "IN",
    }
    for key, code in country_map.items():
        if key in region_lower:
            return code
    return "US"  # default


def _estimate_price(followers, engagement_rate):
    """根据粉丝量和互动率估算报价（美元）"""
    f = followers or 0
    er = engagement_rate or 3.0

    if f < 10000:
        base = 100
    elif f < 50000:
        base = 250
    elif f < 100000:
        base = 500
    elif f < 300000:
        base = 1000
    elif f < 500000:
        base = 2000
    else:
        base = 3500

    # 互动率高的溢价
    if er >= 5.0:
        base *= 1.3
    elif er >= 4.0:
        base *= 1.1

    return round(base)


def _extract_tags_from_posts(posts):
    """从帖子中提取hashtags作为标签"""
    tags = set()
    for post in posts[:5]:
        for h in post.get("hashtags", []) or []:
            name = h.get("name", "")
            if name and len(name) > 2:
                tags.add(name.replace("#", ""))
    return list(tags)[:6]


def _calc_engagement(post_list):
    """从帖子列表计算平均互动数据"""
    if not post_list:
        return {"avg_likes": 0, "avg_comments": 0, "avg_views": 0, "engagement_rate": "3.0%"}

    total_likes = 0
    total_comments = 0
    total_views = 0
    count = len(post_list)

    for post in post_list:
        total_likes += post.get("diggCount", 0) or 0
        total_comments += post.get("commentCount", 0) or 0
        total_views += post.get("playCount", 0) or 0

    avg_likes = total_likes // count
    avg_comments = total_comments // count
    avg_views = total_views // count

    # 互动率 = (点赞+评论) / 播放量
    if avg_views > 0:
        er = round((avg_likes + avg_comments) / avg_views * 100, 1)
    else:
        er = 3.0

    return {
        "avg_likes": avg_likes,
        "avg_comments": avg_comments,
        "avg_views": avg_views,
        "engagement_rate": f"{er}%"
    }


# ─── TikTok 转换 ────────────────────────────────────────

def transform_tiktok(raw):
    """TikTok Apify raw → 统一 Influencer 格式"""
    author = raw.get("author", {})
    posts = raw.get("posts", [])

    followers = author.get("fans", 0) or 0
    engagement = _calc_engagement(posts)
    tags = _extract_tags_from_posts(posts)
    bio = author.get("signature", "") or ""
    posts_text = " ".join(p.get("text", "") for p in posts[:5])
    niche = _infer_niche(tags, bio, posts_text)
    country = _infer_country(author.get("region", "") or "")
    price = _estimate_price(followers, float(engagement["engagement_rate"].replace("%", "")))

    return {
        "id": f"tt_apify_{author.get('id', 'unknown')}",
        "platform": "tiktok",
        "nickname": f"@{author.get('name', 'unknown')}",
        "real_name": author.get("nickName", "") or author.get("name", ""),
        "avatar": author.get("avatar", ""),
        "followers": _format_number(followers),
        "followers_num": followers,
        "avg_likes": _format_number(engagement["avg_likes"]),
        "avg_comments": engagement["avg_comments"],
        "avg_views": _format_number(engagement["avg_views"]),
        "engagement_rate": engagement["engagement_rate"],
        "price_usd": price,
        "price_display": f"${price:,}",
        "tags": tags,
        "region": author.get("region", "未知"),
        "country": country,
        "bio": bio,
        "past_brands": [],
        "fake_risk": 20,
        "niche": niche,
        "language": "English",
        "email": None,
        "_source": "apify"
    }


# ─── Instagram 转换 ────────────────────────────────────────

def transform_instagram(raw):
    """Instagram Apify raw → 统一 Influencer 格式"""
    profile = raw.get("profile", {})

    followers = profile.get("followersCount", 0) or 0
    # IG scraper 返回 details 时可能没有互动数据，给默认值
    posts_count = profile.get("postsCount", 0) or 0
    bio = profile.get("biography", "") or ""
    full_name = profile.get("fullName", "") or profile.get("username", "")
    username = profile.get("username", "")
    is_verified = profile.get("isVerified", False)

    # IG 数据没有那么精确的互动信息，用粉丝量估算
    if followers > 0:
        est_er = 4.5 if followers < 50000 else (3.5 if followers < 300000 else 2.8)
    else:
        est_er = 3.0

    avg_likes = int(followers * est_er / 100) if followers > 0 else 0

    # 推断标签
    bio_lower = bio.lower()
    tags = []
    niche_map = {
        "beauty": ["beauty", "makeup", "skincare", "cosmetics"],
        "fashion": ["fashion", "style", "clothing", "outfit"],
        "fitness": ["fitness", "workout", "gym", "health"],
        "food": ["food", "cooking", "recipe", "chef"],
        "travel": ["travel", "adventure", "wanderlust", "explore"],
        "tech": ["tech", "gadget", "technology", "digital"],
        "lifestyle": ["lifestyle", "blogger", "creator"],
        "parenting": ["mom", "dad", "parent", "baby", "family"],
        "pets": ["pet", "dog", "cat", "animal"],
    }
    for niche_name, kws in niche_map.items():
        if any(kw in bio_lower for kw in kws):
            tags.append(niche_name)

    niche = _infer_niche(tags, bio, "")
    country = _infer_country(profile.get("location", "") or "")
    price = _estimate_price(followers, est_er)

    return {
        "id": f"ig_apify_{username}",
        "platform": "instagram",
        "nickname": f"@{username}",
        "real_name": full_name,
        "avatar": profile.get("profilePicUrl", ""),
        "followers": _format_number(followers),
        "followers_num": followers,
        "avg_likes": _format_number(avg_likes),
        "avg_comments": int(avg_likes * 0.15) if avg_likes > 0 else 0,
        "avg_views": _format_number(avg_likes * 3) if avg_likes > 0 else "0",
        "engagement_rate": f"{est_er}%",
        "price_usd": price,
        "price_display": f"${price:,}",
        "tags": tags,
        "region": profile.get("location", "未知"),
        "country": country,
        "bio": bio,
        "past_brands": [],
        "fake_risk": 15 if is_verified else 25,
        "niche": niche,
        "language": "English",
        "email": profile.get("externalUrl", None) or None,
        "_source": "apify",
        "verified": is_verified
    }
