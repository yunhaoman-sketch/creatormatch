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


def _infer_country(region_or_bio_text):
    """从地区文本+简介文本推断国家代码（增强版：同时检查城市、州、emoji国旗等）"""
    text = (region_or_bio_text or "").lower()

    # 国旗emoji → 国家代码
    flag_map = {
        "\U0001f1fa\U0001f1f8": "US", "\U0001f1ec\U0001f1e7": "UK",
        "\U0001f1e8\U0001f1e6": "CA", "\U0001f1e6\U0001f1fa": "AU",
        "\U0001f1eb\U0001f1f7": "FR", "\U0001f1e9\U0001f1ea": "DE",
        "\U0001f1ef\U0001f1f5": "JP", "\U0001f1f0\U0001f1f7": "KR",
        "\U0001f1f8\U0001f1ec": "SG", "\U0001f1f9\U0001f1ed": "TH",
        "\U0001f1e6\U0001f1ea": "AE", "\U0001f1ea\U0001f1f8": "ES",
        "\U0001f1f3\U0001f1f1": "NL", "\U0001f1f8\U0001f1ea": "SE",
        "\U0001f1e7\U0001f1f7": "BR", "\U0001f1f2\U0001f1fd": "MX",
        "\U0001f1ee\U0001f1e9": "ID", "\U0001f1ee\U0001f1f3": "IN",
    }
    for flag, code in flag_map.items():
        if flag in text:
            return code

    # 国名 + 城市名 + 州名 → 国家代码
    country_map = {
        # US
        "united states": "US", "usa": "US", "america": "US", "us": "US",
        "los angeles": "US", "new york": "US", "miami": "US", "chicago": "US",
        "houston": "US", "atlanta": "US", "dallas": "US", "san francisco": "US",
        "sf": "US", "nyc": "US", "la ": "US", "california": "US", "texas": "US",
        "florida": "US", "new jersey": "US", "washington": "US",
        # UK
        "united kingdom": "UK", "uk": "UK", "britain": "UK", "england": "UK",
        "london": "UK", "manchester": "UK", "birmingham": "UK", "scotland": "UK",
        # Canada
        "canada": "CA", "ca": "CA", "toronto": "CA", "vancouver": "CA", "montreal": "CA",
        # Australia
        "australia": "AU", "au": "AU", "sydney": "AU", "melbourne": "AU", "brisbane": "AU",
        # Europe
        "france": "FR", "paris": "FR",
        "germany": "DE", "berlin": "DE",
        "spain": "ES", "barcelona": "ES", "madrid": "ES",
        "netherlands": "NL", "amsterdam": "NL", "holland": "NL",
        "sweden": "SE", "stockholm": "SE",
        "italy": "IT", "milan": "IT", "rome": "IT",
        # Asia
        "japan": "JP", "tokyo": "JP", "osaka": "JP",
        "south korea": "KR", "korea": "KR", "seoul": "KR",
        "singapore": "SG", "sg": "SG",
        "thailand": "TH", "bangkok": "TH",
        "indonesia": "ID", "jakarta": "ID", "bali": "ID",
        "india": "IN", "mumbai": "IN", "delhi": "IN",
        "philippines": "PH", "manila": "PH",
        "malaysia": "MY", "kuala lumpur": "MY",
        "vietnam": "VN", "ho chi minh": "VN",
        # Middle East
        "uae": "AE", "dubai": "AE", "abu dhabi": "AE",
        "saudi": "SA", "riyadh": "SA",
        # Latin America
        "brazil": "BR", "brasil": "BR", "sao paulo": "BR", "rio": "BR",
        "mexico": "MX", "mexico city": "MX",
    }
    for key, code in country_map.items():
        if key in text:
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
    country = _infer_country((author.get("region", "") or "") + " " + bio)
    price = _estimate_price(followers, float(engagement["engagement_rate"].replace("%", "")))

    return {
        "id": f"tt_apify_{author.get('id', 'unknown')}",
        "platform": "tiktok",
        "nickname": f"@{author.get('name', 'unknown')}",
        "real_name": author.get("nickName", "") or author.get("name", ""),
        "avatar": author.get("avatar", ""),
        "profile_url": f"https://www.tiktok.com/@{author.get('name', '')}",
        "followers": _format_number(followers),
        "followers_num": followers,
        "avg_likes": _format_number(engagement["avg_likes"]),
        "avg_comments": engagement["avg_comments"],
        "avg_views": _format_number(engagement["avg_views"]),
        "engagement_rate": engagement["engagement_rate"],
        "price_usd": price,
        "price_display": f"${price:,}",
        "price_note": "基于粉丝量+互动率的行业基准估算，实际价格以沟通为准",
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
    location_hint = (profile.get("location", "") or "") + " " + bio
    country = _infer_country(location_hint)
    price = _estimate_price(followers, est_er)

    return {
        "id": f"ig_apify_{username}",
        "platform": "instagram",
        "nickname": f"@{username}",
        "real_name": full_name,
        "avatar": profile.get("profilePicUrl", ""),
        "profile_url": f"https://www.instagram.com/{username}/",
        "followers": _format_number(followers),
        "followers_num": followers,
        "avg_likes": _format_number(avg_likes),
        "avg_comments": int(avg_likes * 0.15) if avg_likes > 0 else 0,
        "avg_views": _format_number(avg_likes * 3) if avg_likes > 0 else "0",
        "engagement_rate": f"{est_er}%",
        "price_usd": price,
        "price_display": f"${price:,}",
        "price_note": "基于粉丝量+互动率的行业基准估算，实际价格以沟通为准",
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
