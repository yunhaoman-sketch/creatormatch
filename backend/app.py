from flask import Flask, request, jsonify
from flask_cors import CORS
import random
import sqlite3
import json
import os
import re
import urllib.request
import urllib.parse
import smtplib
import socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import base64

# ─── SMTP 预设 ──────────────────────────────────────────────
SMTP_PRESETS = {
    "gmail": {"label": "Gmail", "host": "smtp.gmail.com", "port": 587, "use_ssl": False},
    "outlook": {"label": "Outlook/Hotmail", "host": "smtp-mail.outlook.com", "port": 587, "use_ssl": False},
    "qq": {"label": "QQ邮箱", "host": "smtp.qq.com", "port": 587, "use_ssl": False},
    "163": {"label": "163邮箱", "host": "smtp.163.com", "port": 465, "use_ssl": True},
    "126": {"label": "126邮箱", "host": "smtp.126.com", "port": 465, "use_ssl": True},
    "custom": {"label": "自定义", "host": "", "port": 587, "use_ssl": False},
}

app = Flask(__name__)

# ─── 动态 CORS 配置 ──────────────────────────────────────────────
_FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "*")
if _FRONTEND_ORIGIN == "*":
    CORS(app)
else:
    CORS(app, origins=[_FRONTEND_ORIGIN, "http://localhost:3000", "http://localhost:5500", "http://127.0.0.1:5500"])

from data import (
    TIKTOK_INFLUENCERS, INSTAGRAM_INFLUENCERS, XIAOHONGSHU_INFLUENCERS,
    ALL_INFLUENCERS, NICHE_KEYWORDS, MARKET_COUNTRY_MAP
)
from apify_client import ApifyClient
from transform import transform_tiktok, transform_instagram

# ─── Apify 配置 ───────────────────────────────────────────────
APIFY_TOKEN = os.environ.get("APIFY_API_TOKEN", "")
_apify_client = None

def get_apify_client():
    global _apify_client
    if _apify_client is None and APIFY_TOKEN:
        _apify_client = ApifyClient(APIFY_TOKEN)
    return _apify_client

# ─── 汇率常量 ──────────────────────────────────────────────────
USD_TO_CNY = 7.25

# ─── 数据库初始化 ───────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "hunter.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            influencer_id TEXT,
            platform TEXT,
            nickname TEXT,
            data TEXT,
            email_status TEXT DEFAULT 'pending',
            email_sent_at TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS email_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            influencer_id TEXT,
            subject TEXT,
            body TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migration: add email columns for existing databases
    try:
        c.execute("ALTER TABLE candidates ADD COLUMN email_status TEXT DEFAULT 'pending'")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE candidates ADD COLUMN email_sent_at TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()

# ─── 水军风险引擎 ───────────────────────────────────────────────
def compute_fake_risk(inf):
    reasons = []
    base = int(inf.get("fake_risk", 20) * 0.4)
    score = base

    try:
        likes_raw = inf.get("avg_likes", "0")
        likes_num = parse_knum(likes_raw)
        comments_num = inf.get("avg_comments", 0)
        if likes_num > 0:
            ratio = comments_num / likes_num
            if ratio < 0.02:
                score += 40
                reasons.append("评论/点赞比 {:.1%}，远低于正常水平（>2%），疑似买赞".format(ratio))
            elif ratio < 0.04:
                score += 15
                reasons.append("评论/点赞比偏低（{:.1%}），互动质量一般".format(ratio))
    except Exception:
        pass

    try:
        er = float(inf.get("engagement_rate", "3%").replace("%", ""))
        followers_num = inf.get("followers_num", 0)
        if followers_num > 300000 and er < 2.0:
            score += 30
            reasons.append("粉丝量{}但互动率仅{:.1f}%，粉丝活跃度存疑".format(
                inf.get("followers", ""), er))
        elif er < 1.0:
            score += 20
            reasons.append("互动率低于1%，账号活跃度异常")
    except Exception:
        pass

    if not inf.get("past_brands") or len(inf.get("past_brands", [])) == 0:
        score += 5
        reasons.append("无已知历史合作品牌记录")

    score = min(100, max(0, score))
    if score < 15 and not reasons:
        reasons.append("数据健康，各项指标正常")
    elif score < 30 and not reasons:
        reasons.append("互动数据基本正常，风险可控")

    return score, reasons


def parse_knum(s):
    s = str(s).lower().strip()
    if s.endswith("m"):
        return float(s[:-1]) * 1_000_000
    if s.endswith("k"):
        return float(s[:-1]) * 1_000
    return float(s.replace(",", ""))


def enrich_influencer(inf):
    inf = dict(inf)
    price_usd = inf.get("price_usd")
    price_rmb = inf.get("price_rmb")
    if price_usd and not price_rmb:
        inf["price_rmb"] = round(price_usd * USD_TO_CNY)
        inf["price_cny_display"] = "¥{:,}".format(inf["price_rmb"])
    elif price_rmb and not price_usd:
        inf["price_usd"] = round(price_rmb / USD_TO_CNY)
        inf["price_usd_display"] = "${:,}".format(inf["price_usd"])
    if price_usd:
        inf["price_usd_display"] = "${:,}".format(price_usd)
    if inf.get("price_rmb"):
        inf["price_cny_display"] = "¥{:,}".format(inf["price_rmb"])

    risk_score, risk_reasons = compute_fake_risk(inf)
    inf["fake_risk_score"] = risk_score
    inf["fake_risk_reasons"] = risk_reasons
    if risk_score < 30:
        inf["fake_risk_level"] = "green"
        inf["fake_risk_label"] = "低风险"
    elif risk_score < 70:
        inf["fake_risk_level"] = "yellow"
        inf["fake_risk_label"] = "中等风险"
    else:
        inf["fake_risk_level"] = "red"
        inf["fake_risk_label"] = "高风险"

    followers_num = inf.get("followers_num", 0)
    if followers_num > 500000:
        inf["tier"] = "head"
        inf["tier_label"] = "头部 >500k"
    elif followers_num >= 50000:
        inf["tier"] = "mid"
        inf["tier_label"] = "腰部 50k-500k"
    else:
        inf["tier"] = "tail"
        inf["tier_label"] = "尾部 <50k"

    return inf


# ─── LLM 解析用户输入 ──────────────────────────────────────────
def parse_user_input_with_llm(user_input: str) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY", "")
    api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")

    if api_key:
        try:
            prompt = f"""You are a cross-border e-commerce influencer marketing expert. Parse the following user input and extract key information. Return JSON format.

User input:
{user_input}

Extract these fields, fill null if missing:
{{
  "product_type": "product type (e.g., wigs, skincare, sports gear)",
  "product_keywords": ["related keyword list"],
  "price_range_usd": {{"min": min_price, "max": max_price}},
  "target_market": "target market (e.g., US, Southeast Asia, Global)",
  "target_audience": "target audience description (e.g., African American women aged 18-35)",
  "audience_age_min": min_age or null,
  "audience_age_max": max_age or null,
  "audience_ethnicity": "ethnicity/racial特征或null",
  "audience_gender": "gender倾向(female/male/all)或null",
  "niche_categories": ["related content categories, choose from: beauty/fashion/fitness/tech/food/lifestyle/travel/parenting/pets/shopping/education"],
  "special_requirements": "other special requirements or null",
  "search_summary": "one sentence summary of search intent"
}}

Only return JSON, no other content."""

            payload = json.dumps({
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 600
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{api_base}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                content = result["choices"][0]["message"]["content"].strip()
                content = re.sub(r"```json\s*|\s*```", "", content).strip()
                parsed = json.loads(content)
                parsed["_source"] = "llm"
                return parsed
        except Exception as e:
            print(f"[LLM] 解析失败，降级到规则模式: {e}")

    return rule_based_parse(user_input)


def rule_based_parse(user_input: str) -> dict:
    text_lower = user_input.lower()
    result = {
        "_source": "rule",
        "product_type": None,
        "product_keywords": [],
        "price_range_usd": None,
        "target_market": "全球",
        "target_audience": None,
        "audience_age_min": None,
        "audience_age_max": None,
        "audience_ethnicity": None,
        "audience_gender": None,
        "niche_categories": [],
        "special_requirements": None,
        "search_summary": user_input[:80]
    }

    market_keywords = [
        (["美国", "us", "america", "united states"], "美国"),
        (["英国", "uk", "britain", "england"], "英国"),
        (["东南亚", "southeast asia", "sea", "东南", "泰国", "新加坡", "马来"], "东南亚"),
        (["欧洲", "europe", "european", "法国", "德国", "西班牙", "荷兰", "瑞典"], "欧洲"),
        (["澳大利亚", "澳洲", "australia", "aus"], "澳大利亚"),
        (["日本", "japan", "jp"], "日本"),
        (["韩国", "korea", "kr"], "韩国"),
        (["中东", "dubai", "uae", "middle east"], "中东"),
        (["北美", "north america"], "北美"),
        (["亚太", "asia pacific"], "亚太"),
        (["全球", "global", "worldwide"], "全球"),
    ]
    for keywords_list, market in market_keywords:
        if any(kw in text_lower or kw in user_input for kw in keywords_list):
            result["target_market"] = market
            break

    price_patterns = [
        r'\$\s*(\d+)\s*[-~到]\s*\$?\s*(\d+)',
        r'(\d+)\s*[-~到]\s*(\d+)\s*(?:美元|usd)',
        r'(?:客单价|价格|price)[^\d]*(\d+)[^\d]*[-~到][^\d]*(\d+)',
    ]
    for pat in price_patterns:
        m = re.search(pat, user_input, re.I)
        if m:
            result["price_range_usd"] = {"min": int(m.group(1)), "max": int(m.group(2))}
            break

    age_patterns = [
        r'(\d{1,2})\s*[-~到]\s*(\d{1,2})\s*岁',
        r'aged?\s+(\d{1,2})\s*[-–到~]\s*(\d{1,2})',
        r'(\d{1,2})-(\d{1,2})\s*(?:years?|岁)',
    ]
    for pat in age_patterns:
        m = re.search(pat, user_input, re.I)
        if m:
            result["audience_age_min"] = int(m.group(1))
            result["audience_age_max"] = int(m.group(2))
            break

    female_kws = ["女性", "女生", "女人", "姐姐", "妈妈", "female", "women", "woman", "girl", "ladies", "她们"]
    male_kws = ["男性", "男生", "男人", "爸爸", "male", "men", "man", "boy", "guys", "他们"]
    if any(w in user_input or w in text_lower for w in female_kws):
        result["audience_gender"] = "female"
    elif any(w in user_input or w in text_lower for w in male_kws):
        result["audience_gender"] = "male"

    ethnicity_map = [
        (["黑人", "非裔", "black", "african", "afro"], "African American"),
        (["亚裔", "亚洲", "asian", "east asian"], "Asian"),
        (["拉丁", "latina", "latino", "hispanic"], "Latina/Latino"),
        (["白人", "欧美", "white", "caucasian"], "White"),
    ]
    for kws, eth in ethnicity_map:
        if any(kw in user_input or kw in text_lower for kw in kws):
            result["audience_ethnicity"] = eth
            break

    EXTRA_NICHE_MAP = {
        "beauty": ["假发", "发套", "发型", "wig", "hair", "头发", "发", "护肤", "美妆", "彩妆", "化妆", "口红", "粉底", "眼影"],
        "fashion": ["服装", "衣服", "穿搭", "时尚", "鞋", "包", "配饰", "服饰"],
        "fitness": ["健身", "运动", "瑜伽", "减脂", "增肌", "蛋白粉"],
        "tech": ["手机", "耳机", "电脑", "数码", "充电", "电子", "智能"],
        "food": ["食品", "零食", "饮料", "咖啡", "茶", "食物", "美食", "餐"],
        "parenting": ["母婴", "宝宝", "婴儿", "育儿", "奶粉", "辅食"],
        "pets": ["宠物", "猫", "狗", "宠"],
        "lifestyle": ["生活", "家居", "家装"],
        "travel": ["旅行", "旅游", "出行", "背包"],
        "shopping": ["购物", "好物", "省钱", "折扣"],
        "education": ["教育", "学习", "留学", "考试"],
    }

    combined_map = {}
    for niche, kws in NICHE_KEYWORDS.items():
        combined_map[niche] = list(set(kws + EXTRA_NICHE_MAP.get(niche, [])))

    found_niches = []
    for niche, keywords in combined_map.items():
        for kw in keywords:
            if kw in user_input or kw in text_lower:
                if niche not in found_niches:
                    found_niches.append(niche)
                if kw not in result["product_keywords"]:
                    result["product_keywords"].append(kw)
    result["niche_categories"] = found_niches

    product_patterns = [
        r'产品[：:是为]\s*([^\s，,。.\n]{1,12})',
        r'product[:\s]+([^\s,.]{1,20})',
        r'推广[的\s]*([^\s，,。.]{1,10})',
        r'销售[的\s]*([^\s，,。.]{1,10})',
    ]
    for pat in product_patterns:
        m = re.search(pat, user_input, re.I)
        if m:
            result["product_type"] = m.group(1).strip()
            break
    if not result["product_type"] and result["product_keywords"]:
        result["product_type"] = result["product_keywords"][0]

    return result


# ─── AI 匹配核心 ───────────────────────────────────────────────
def advanced_match(platform_list, parsed_input, filters=None, limit=10):
    filters = filters or {}
    tier_filter = filters.get("tier")
    budget_min = filters.get("budget_min")
    budget_max = filters.get("budget_max")
    region_filter = filters.get("region")

    niches = parsed_input.get("niche_categories", [])
    target_market = parsed_input.get("target_market", "全球")
    audience_gender = parsed_input.get("audience_gender")
    audience_ethnicity = parsed_input.get("audience_ethnicity")
    price_range = parsed_input.get("price_range_usd")
    product_keywords = [k.lower() for k in parsed_input.get("product_keywords", [])]

    target_countries = []
    for market, countries in MARKET_COUNTRY_MAP.items():
        if market in target_market:
            target_countries.extend(countries)
    if not target_countries:
        target_countries = [inf["country"] for inf in platform_list]

    scored = []
    for inf in platform_list:
        inf_e = enrich_influencer(inf)
        score = 0
        reasons = []

        if tier_filter and inf_e.get("tier") != tier_filter:
            continue
        if budget_min is not None and inf_e.get("price_usd"):
            if inf_e["price_usd"] < budget_min:
                continue
        if budget_max is not None and inf_e.get("price_usd"):
            if inf_e["price_usd"] > budget_max:
                continue
        if region_filter and region_filter != "其他":
            region_countries = MARKET_COUNTRY_MAP.get(region_filter, [])
            if region_countries and inf_e["country"] not in region_countries:
                continue

        if inf_e["niche"] in niches:
            score += 40
            reasons.append(f"内容领域完全匹配「{inf_e['niche']}」")
        elif product_keywords:
            tag_text = " ".join(inf_e.get("tags", [])).lower()
            bio_text = inf_e.get("bio", "").lower()
            kw_hit = sum(1 for kw in product_keywords if kw in tag_text or kw in bio_text)
            if kw_hit > 0:
                score += min(30, kw_hit * 10)
                reasons.append(f"内容标签含 {kw_hit} 个相关关键词")
            else:
                score += 5

        if inf_e["country"] in target_countries:
            score += 25
            reasons.append(f"地区匹配（{inf_e['region']}）")
        else:
            score += 5

        audience_score = 0
        bio_lower = inf_e.get("bio", "").lower()
        tags_lower = " ".join(inf_e.get("tags", [])).lower()
        combined = bio_lower + " " + tags_lower + " " + inf_e.get("nickname", "").lower()

        female_signals = ["women", "girl", "female", "she", "ladies", "mama", "mom", "妈", "女", "beauty", "makeup", "skincare"]
        male_signals = ["men", "guy", "male", "he", "gym", "bro", "dad", "爸", "男"]
        if audience_gender == "female":
            if any(s in combined for s in female_signals):
                audience_score += 10
                reasons.append("账号受众以女性为主，与目标群体一致")
        elif audience_gender == "male":
            if any(s in combined for s in male_signals):
                audience_score += 10

        ethnicity_signals = {
            "African American": ["black", "afro", "melanin", "natural hair", "braids", "locs", "weave", "wig"],
            "Asian": ["asian", "k-beauty", "j-beauty", "korean", "japanese", "chinese", "中"],
            "Latina/Latino": ["latina", "latin", "hispanic", "spanish"],
            "White": ["european", "nordic", "scandinavian", "french", "british"]
        }
        if audience_ethnicity and audience_ethnicity in ethnicity_signals:
            signals = ethnicity_signals[audience_ethnicity]
            if any(s in combined for s in signals):
                audience_score += 10
                reasons.append(f"受众文化背景与「{audience_ethnicity}」群体高度契合")
        brand_text = " ".join(inf_e.get("past_brands", [])).lower()
        if product_keywords:
            brand_hit = sum(1 for kw in product_keywords if kw in brand_text)
            if brand_hit > 0:
                audience_score += 5
                reasons.append(f"历史合作品牌与当前产品类型相关")

        score += min(20, audience_score)

        try:
            er = float(inf_e.get("engagement_rate", "3%").replace("%", ""))
            er_score = min(10, int(er * 2))
            score += er_score
            if er >= 4.5:
                reasons.append(f"互动率 {inf_e['engagement_rate']} 表现优秀")
            elif er >= 3.5:
                reasons.append(f"互动率 {inf_e['engagement_rate']} 健康稳定")
        except Exception:
            pass

        risk = inf_e.get("fake_risk_score", 20)
        score -= min(15, risk // 7)

        if price_range and inf_e.get("price_usd"):
            p = inf_e["price_usd"]
            pmin, pmax = price_range["min"], price_range["max"]
            target_min = pmin * 2
            target_max = pmax * 8
            if target_min <= p <= target_max:
                score += 5
                reasons.append(f"报价 {inf_e.get('price_usd_display','')} 与产品客单价匹配")

        if inf_e.get("past_brands") and len(inf_e["past_brands"]) >= 2:
            if not any("历史" in r for r in reasons):
                reasons.append(f"曾合作过 {', '.join(inf_e['past_brands'][:2])} 等品牌")

        match_score = min(99, max(50, int(score * 1.0)))
        inf_e["match_score"] = match_score
        inf_e["match_reason"] = "；".join(reasons) if reasons else "综合评估适合"
        inf_e["match_reasons_list"] = reasons

        scored.append((score, inf_e))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [inf for _, inf in scored[:limit]]


# ─── LLM 话术生成 ───────────────────────────────────────────────
def generate_script_with_llm(inf, parsed_input, brand_name="Your Brand",
                              language="en", tone="friendly", extra_requirements=""):
    """调用大模型生成多语言话术，失败返回 None"""
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY", "")
    api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
    if not api_key:
        return None

    platform_map = {"tiktok": "TikTok", "instagram": "Instagram", "xiaohongshu": "小红书"}
    platform_name = platform_map.get(inf["platform"], "social media")
    product_type = parsed_input.get("product_type") or "our product"
    audience = parsed_input.get("target_audience", "")
    niche = inf.get("niche", "")
    tags = ", ".join((inf.get("tags") or [])[:3])
    past_brands = ", ".join((inf.get("past_brands") or [])[:2])
    region = inf.get("region", "")
    price = inf.get("price_usd_display") or inf.get("price_display", "competitive rates")

    tone_map = {
        "professional": "professional, formal, respectful business tone",
        "friendly": "warm, friendly, casual and approachable tone",
        "direct": "direct, concise, deal-focused business tone — get to the point quickly"
    }
    tone_desc = tone_map.get(tone, tone_map["friendly"])

    if language == "zh":
        system_prompt = "你是一位专业的海外达人营销专家，擅长撰写合作邀约邮件。"
        user_prompt = f"""请用{tone_desc}撰写一封给达人的合作邀约邮件（中文）。

达人信息：
- 平台：{platform_name}
- 昵称：{inf.get('nickname', '')}
- 真名：{inf.get('real_name', inf.get('nickname', ''))}
- 地区：{region}
- 内容领域：{niche}
- 内容标签：{tags}
- 历史合作品牌：{past_brands or '无'}
- 预估报价：{price}

产品信息：
- 产品类型：{product_type}
- 目标受众：{audience or '未指定'}

品牌名称：{brand_name}
额外要求：{extra_requirements or '无'}

要求：
1. 邮件语气：{tone_desc}
2. 突出产品与达人受众的契合点
3. 提及合作形式和免费样品
4. 包含报价参考
5. 不超过200字，简洁有力
6. 只输出邮件正文，不要额外说明"""
    else:
        system_prompt = "You are a professional influencer marketing specialist who writes outreach emails for brand collaboration campaigns."
        user_prompt = f"""Write a collaboration outreach email in English using a {tone_desc}.

Influencer Info:
- Platform: {platform_name}
- Name: {inf.get('real_name', inf.get('nickname', ''))}
- Handle: {inf.get('nickname', '')}
- Region: {region}
- Niche: {niche}
- Content tags: {tags}
- Past brand collabs: {past_brands or 'N/A'}
- Estimated rate: {price}

Campaign Info:
- Product: {product_type}
- Target audience: {audience or 'general audience'}

Brand: {brand_name}
Extra requirements: {extra_requirements or 'None'}

Requirements:
1. Tone: {tone_desc}
2. Highlight why this influencer is a great fit
3. Mention free product sample and collaboration format
4. Reference their past work if available
5. Keep it under 150 words, concise and genuine
6. Output only the email body, no extra commentary"""

    try:
        payload = json.dumps({
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 500
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{api_base}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            script = result["choices"][0]["message"]["content"].strip()
            return script
    except Exception as e:
        print(f"[LLM] 话术生成失败: {e}")
        return None


def generate_outreach_script(inf, parsed_input, brand_name="Your Brand",
                            language="en", tone="friendly", extra_requirements=""):
    """生成针对性沟通话术（多语言版）"""
    # 优先尝试 LLM 生成
    llm_script = generate_script_with_llm(inf, parsed_input, brand_name, language, tone, extra_requirements)
    if llm_script:
        return llm_script

    # 降级：规则模板
    platform_map = {"tiktok": "TikTok", "instagram": "Instagram", "xiaohongshu": "小红书"}
    platform_name = platform_map.get(inf["platform"], "social media")
    product_type = parsed_input.get("product_type") or parsed_input.get("search_summary", "our product")
    audience = parsed_input.get("target_audience", "")
    niche = inf.get("niche", "")
    tags = (inf.get("tags") or [])[:2]
    past_brands = (inf.get("past_brands") or [])[:2]

    extra_line = f"\n\n📝 额外说明：{extra_requirements}" if extra_requirements else ""

    if language == "zh" or inf["platform"] == "xiaohongshu":
        if tone == "professional":
            greeting = f"尊敬的 {inf.get('real_name', inf['nickname'])}，"
            intro = f"我们是{brand_name}，专注于{product_type}领域。经过慎重评估，我们认为您的内容风格与我们的品牌理念高度契合。"
        elif tone == "direct":
            greeting = f"{inf.get('real_name', inf['nickname'])}你好，"
            intro = f"{brand_name}正在推广{product_type}，目标受众为{audience or '广泛群体'}，认为与您的合作效率会很高。"
        else:
            greeting = f"{inf.get('real_name', inf['nickname'])}你好！"
            intro = f"一直在关注您在{platform_name}上关于「{(tags or ['好物分享'])[0]}」的内容，非常喜欢您的分享风格！我们目前正在推广一款产品：**{product_type}**{f'，主要面向 {audience}' if audience else ''}。"

        script = f"""{greeting}

{intro}

💼 合作形式：图文笔记 / 视频开箱 / 测评均可
💰 报价参考：{inf.get('price_display', inf.get('price_cny_display', '面议'))}
📦 我们提供免费样品供真实体验后分享{extra_line}

如果感兴趣，欢迎回复这条消息或发邮件到我们的合作邮箱，我们可以进一步详聊 😊

期待与您合作！
{brand_name} 品牌合作团队"""
        return script

    # 英文模板（支持三种语气）
    niche_phrases = {
        "beauty": "beauty & skincare", "fitness": "fitness & wellness",
        "tech": "tech & gadgets", "food": "food & cooking",
        "fashion": "fashion & style", "lifestyle": "lifestyle",
        "travel": "travel", "parenting": "parenting & family",
        "pets": "pet care", "shopping": "shopping & deals",
        "education": "educational content"
    }
    niche_phrase = niche_phrases.get(niche, "content creation")
    audience_line = f" targeting {audience}" if audience else ""
    price_line = inf.get("price_usd_display") or inf.get("price_display", "competitive rates")
    cny_line = f" (≈ {inf.get('price_cny_display', '')})" if inf.get("price_cny_display") else ""
    extra_en = f"\n\nP.S. {extra_requirements}" if extra_requirements else ""

    if tone == "professional":
        script = f"""Dear {inf.get('real_name', inf['nickname'])},

I hope this message finds you well. I am writing on behalf of **{brand_name}**, where we are preparing to launch our latest **{product_type}** campaign{audience_line}.

After reviewing your {platform_name} profile, your expertise in {niche_phrase} and the high engagement from your audience in {region} make you an ideal partner for this collaboration.

**Collaboration Details:**
- Compensation: {price_line}{cny_line} per sponsored post
- Product: Free samples provided (yours to keep)
- Creative direction: Entirely your call — we value your authentic voice
- Timeline: Flexible around your content calendar{extra_en}

Would you be available for a brief discussion this week? I would be happy to share our campaign brief and answer any questions.

Thank you for your time and consideration.

Best regards,
{brand_name} Partnerships Team"""
    elif tone == "direct":
        script = f"""Hi {inf.get('real_name', inf['nickname'])},

Quick question — are you open to a paid collaboration for **{brand_name}**'s new {product_type} campaign{audience_line}?

Why you: Your {niche_phrase} content hits exactly the right audience. Engagement looks strong in {region}.

Offer:
- {price_line}{cny_line} per post
- Free product, no strings
- Fast turnaround, minimal back-and-forth{extra_en}

Available to chat this week? Let me know and I'll send over the brief.

Thanks,
{brand_name}"""
    else:
        script = f"""Hi {inf.get('real_name', inf['nickname'])},

I've been following your {platform_name} content and I'm really impressed by your {niche_phrase} expertise — your audience engagement speaks for itself!

I'm reaching out on behalf of **{brand_name}**. We're launching a campaign for our **{product_type}** product{audience_line}, and after reviewing your profile, I believe you'd be a fantastic fit.

**What we're offering:**
✅ Compensation: {price_line}{cny_line} per sponsored post
✅ Free products to keep — no purchase required
✅ Full creative freedom, your voice and style
✅ Opportunity for a long-term ambassador relationship{extra_en}

Would you be open to a quick 15-min call this week to discuss? I'd love to share more details about the campaign brief.

Looking forward to hearing from you!

Best,
{brand_name} Partnerships Team"""

    return script


# ─── API 路由 ──────────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    has_llm = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY"))
    has_apify = bool(os.environ.get("APIFY_API_TOKEN", ""))
    return jsonify({
        "status": "ok",
        "message": "AI达人猎手 API 运行中",
        "llm_enabled": has_llm,
        "apify_enabled": has_apify,
        "version": "3.1"
    })

@app.route("/api/influencers/<platform>", methods=["GET"])
def get_influencers(platform):
    data_map = {
        "tiktok": TIKTOK_INFLUENCERS,
        "instagram": INSTAGRAM_INFLUENCERS,
        "xiaohongshu": XIAOHONGSHU_INFLUENCERS
    }
    if platform not in data_map:
        return jsonify({"error": "不支持的平台"}), 400
    enriched = [enrich_influencer(inf) for inf in data_map[platform]]
    return jsonify({"data": enriched, "total": len(enriched)})

@app.route("/api/search", methods=["POST"])
def search_influencers():
    body = request.get_json()
    platform = body.get("platform", "tiktok")
    user_input = body.get("user_input", "")
    product_desc = body.get("product_desc", "")
    limit = int(body.get("limit", 10))

    filters = {
        "tier": body.get("tier"),
        "budget_min": body.get("budget_min"),
        "budget_max": body.get("budget_max"),
        "region": body.get("region")
    }

    raw_input = user_input or product_desc
    if not raw_input:
        return jsonify({"error": "请输入产品描述"}), 400

    data_map = {
        "tiktok": TIKTOK_INFLUENCERS,
        "instagram": INSTAGRAM_INFLUENCERS,
        "xiaohongshu": XIAOHONGSHU_INFLUENCERS
    }
    if platform not in data_map:
        return jsonify({"error": "不支持的平台"}), 400

    parsed = parse_user_input_with_llm(raw_input)

    if body.get("target_market") and body["target_market"] != "全球":
        parsed["target_market"] = body["target_market"]

    # ── 尝试 Apify 真实数据（TikTok / Instagram） ──
    data_source = "local"
    results = None

    if platform in ("tiktok", "instagram"):
        client = get_apify_client()
        if client:
            try:
                keywords = parsed.get("product_keywords", [])
                niches = parsed.get("niche_categories", [])
                search_terms = list(dict.fromkeys(keywords + niches))  # 去重保序

                if platform == "tiktok":
                    raw = client.search_tiktok_profiles(search_terms, limit * 3)
                    influencers = [transform_tiktok(r) for r in raw]
                else:
                    raw = client.search_instagram_profiles(search_terms, limit * 3)
                    influencers = [transform_instagram(r) for r in raw]

                if influencers:
                    results = advanced_match(influencers, parsed, filters=filters, limit=limit)
                    data_source = "apify"
            except Exception as e:
                print(f"[Apify] 搜索异常，回退本地数据: {e}")

    # ── 回退：本地模拟数据 ──
    if results is None:
        results = advanced_match(data_map[platform], parsed, filters=filters, limit=limit)

    return jsonify({
        "data": results,
        "total": len(results),
        "platform": platform,
        "parsed_input": parsed,
        "parse_source": parsed.get("_source", "rule"),
        "data_source": data_source
    })

@app.route("/api/outreach", methods=["POST"])
def generate_outreach():
    body = request.get_json()
    influencer_id = body.get("influencer_id")
    user_input = body.get("user_input") or body.get("product_desc", "")
    brand_name = body.get("brand_name", "Your Brand")
    language = body.get("language", "en")
    tone = body.get("tone", "friendly")
    extra_requirements = body.get("extra_requirements", "")

    inf = next((i for i in ALL_INFLUENCERS if i["id"] == influencer_id), None)
    if not inf:
        return jsonify({"error": "达人不存在"}), 404

    inf_e = enrich_influencer(inf)
    parsed = rule_based_parse(user_input) if user_input else {"product_type": "your product", "target_audience": ""}
    script = generate_outreach_script(inf_e, parsed, brand_name, language, tone, extra_requirements)

    # 根据语言生成对应主题行
    if language == "zh":
        subject = f"品牌合作邀请 - {brand_name} × {inf_e['nickname']}"
    else:
        subject = f"Brand Collab Opportunity - {brand_name} x {inf_e['nickname']}"

    return jsonify({"script": script, "subject": subject, "influencer": inf_e})

@app.route("/api/candidates", methods=["GET"])
def get_candidates():
    session_id = request.args.get("session_id", "default")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM candidates WHERE session_id=? ORDER BY added_at DESC", (session_id,))
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        item = {
            "id": row[0], "session_id": row[1], "influencer_id": row[2],
            "platform": row[3], "nickname": row[4],
            "data": json.loads(row[5]),
            "email_status": row[7] or "pending",
            "email_sent_at": row[8] or None,
            "added_at": row[6]
        }
        result.append(item)
    return jsonify({"data": result, "total": len(result)})

@app.route("/api/candidates", methods=["POST"])
def add_candidate():
    body = request.get_json()
    session_id = body.get("session_id", "default")
    influencer_id = body.get("influencer_id")

    inf = next((i for i in ALL_INFLUENCERS if i["id"] == influencer_id), None)
    if not inf:
        return jsonify({"error": "达人不存在"}), 404

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM candidates WHERE session_id=? AND influencer_id=?", (session_id, influencer_id))
    if c.fetchone():
        conn.close()
        return jsonify({"message": "已在候选列表中", "already_exists": True})
    inf_e = enrich_influencer(inf)
    c.execute(
        "INSERT INTO candidates (session_id, influencer_id, platform, nickname, data) VALUES (?,?,?,?,?)",
        (session_id, influencer_id, inf["platform"], inf["nickname"], json.dumps(inf_e, ensure_ascii=False))
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "已加入候选列表", "influencer": inf_e})

@app.route("/api/candidates/<int:candidate_id>", methods=["DELETE"])
def remove_candidate(candidate_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM candidates WHERE id=?", (candidate_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "已移除"})

@app.route("/api/email/test", methods=["POST"])
def test_email_config():
    """测试 SMTP 配置"""
    body = request.get_json()
    config = body.get("config", {})

    host = config.get("host", "")
    port = int(config.get("port", 587))
    username = config.get("username", "")
    password = config.get("password", "")
    use_ssl = config.get("ssl", False)

    if not host or not username or not password:
        return jsonify({"error": "SMTP服务器、邮箱地址和授权码不能为空", "status": "invalid"}), 400

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            server.starttls()

        server.login(username, password)

        # 发送测试邮件给自己
        msg = MIMEText(
            "✅ 如果您收到此邮件，说明您的邮箱配置成功！\n\n"
            "这是来自 AI达人猎手 的自动测试邮件。\n"
            "无需回复此邮件。\n\n"
            "If you receive this email, your SMTP configuration works!\n"
            "This is an automated test from AI Influencer Hunter.",
            "plain", "utf-8"
        )
        msg["Subject"] = "AI达人猎手 - 邮箱配置测试"
        msg["From"] = username
        msg["To"] = username

        server.sendmail(username, username, msg.as_string())
        server.quit()

        return jsonify({"message": "测试邮件发送成功！请检查收件箱", "status": "ok"})
    except smtplib.SMTPAuthenticationError:
        return jsonify({"error": "认证失败：用户名或授权码不正确，请检查后重试", "status": "auth_error"}), 401
    except smtplib.SMTPConnectError:
        return jsonify({"error": f"连接失败：无法连接到 {host}:{port}，请检查服务器地址和端口", "status": "connect_error"}), 500
    except socket.timeout:
        return jsonify({"error": "网络超时：连接 SMTP 服务器超时，请检查网络和端口", "status": "timeout"}), 500
    except smtplib.SMTPServerDisconnected:
        return jsonify({"error": "服务器断开连接：可能是端口或加密方式不正确", "status": "disconnected"}), 500
    except Exception as e:
        return jsonify({"error": f"发送失败：{str(e)}", "status": "error"}), 500

@app.route("/api/email/send", methods=["POST"])
def send_email():
    body = request.get_json()
    influencer_id = body.get("influencer_id")
    subject = body.get("subject", "")
    email_body = body.get("body", "")
    smtp_config = body.get("smtp_config", {})
    attachment = body.get("attachment")  # 可选：{filename, data (base64)}

    inf = next((i for i in ALL_INFLUENCERS if i["id"] == influencer_id), None)
    if not inf:
        return jsonify({"error": "达人不存在"}), 404

    recipient_email = inf.get("email", "")
    if not recipient_email:
        return jsonify({"error": "该达人未预设邮箱地址"}), 400

    # 写入日志（无论成功与否）
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO email_logs (influencer_id, subject, body) VALUES (?,?,?)",
              (influencer_id, subject, email_body))

    # 如果没有 SMTP 配置，只做模拟发送
    if not smtp_config.get("host"):
        c.execute(
            "UPDATE candidates SET email_status='sent', email_sent_at=CURRENT_TIMESTAMP WHERE influencer_id=?",
            (influencer_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({
            "message": f"（模拟）邮件已记录：{recipient_email}",
            "to": recipient_email,
            "subject": subject, "status": "mock_sent"
        })

    # 真实 SMTP 发送
    host = smtp_config.get("host", "")
    port = int(smtp_config.get("port", 587))
    username = smtp_config.get("username", "")
    password = smtp_config.get("password", "")
    use_ssl = smtp_config.get("ssl", False)

    try:
        # 构建邮件
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = username
        msg["To"] = recipient_email
        msg.attach(MIMEText(email_body, "plain", "utf-8"))

        # 附件
        if attachment and attachment.get("data"):
            part = MIMEBase("application", "octet-stream")
            part.set_payload(base64.b64decode(attachment["data"]))
            encoders.encode_base64(part)
            filename = attachment.get("filename", "attachment.pdf")
            part.add_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}")
            msg.attach(part)

        # 发送
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls()

        server.login(username, password)
        server.sendmail(username, recipient_email, msg.as_string())
        server.quit()

        c.execute(
            "UPDATE candidates SET email_status='sent', email_sent_at=CURRENT_TIMESTAMP WHERE influencer_id=?",
            (influencer_id,)
        )
        conn.commit()
        conn.close()

        return jsonify({
            "message": f"邮件已成功发送至 {recipient_email}",
            "to": recipient_email,
            "subject": subject, "status": "sent"
        })
    except smtplib.SMTPAuthenticationError:
        conn.commit()
        conn.close()
        return jsonify({"error": "SMTP认证失败，请检查邮箱地址和授权码", "status": "auth_error"}), 401
    except smtplib.SMTPConnectError:
        conn.commit()
        conn.close()
        return jsonify({"error": f"无法连接到 SMTP 服务器 {host}:{port}，请检查配置", "status": "connect_error"}), 500
    except socket.timeout:
        conn.commit()
        conn.close()
        return jsonify({"error": "发送超时，请检查网络连接和 SMTP 端口", "status": "timeout"}), 500
    except Exception as e:
        conn.commit()
        conn.close()
        return jsonify({"error": f"发送失败：{str(e)}", "status": "error"}), 500

@app.route("/api/stats", methods=["GET"])
def get_stats():
    return jsonify({
        "tiktok": {"total": len(TIKTOK_INFLUENCERS), "label": "TikTok达人"},
        "instagram": {"total": len(INSTAGRAM_INFLUENCERS), "label": "Instagram达人"},
        "xiaohongshu": {"total": len(XIAOHONGSHU_INFLUENCERS), "label": "小红书达人"},
        "total": len(ALL_INFLUENCERS),
        "usd_to_cny": USD_TO_CNY
    })

@app.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "service": "CreatorMatch API", "version": "1.0.0"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") != "production"
    app.run(debug=debug, port=port, host="0.0.0.0")
