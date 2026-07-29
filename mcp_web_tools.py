import os
import re
import time
import json
import asyncio
import datetime
import threading
from difflib import SequenceMatcher
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import quote_plus, urljoin, urldefrag

import requests
import trafilatura
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from mcp.server.fastmcp import FastMCP

try:
    import nest_asyncio
    nest_asyncio.apply()
except Exception:
    nest_asyncio = None

PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "sse")

mcp = FastMCP("information_fetch", host=HOST, port=PORT)

DEFAULT_HEADERS = {
    "User-Agent": os.getenv(
        "WEB_TOOLS_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MOBILE_HEADERS = {
    **DEFAULT_HEADERS,
    "User-Agent": os.getenv(
        "WEB_TOOLS_MOBILE_USER_AGENT",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ),
}

EXCLUDE_PATTERNS = [
    "无人车", "自动驾驶出租车", "Robotaxi", "商用车", "重卡", "轻卡", "二手车",
    "股价", "股票", "港股", "A股", "美股", "涨停", "跌停", "融资融券",
    "保时捷", "迈巴赫", "劳斯莱斯", "宾利", "兰博基尼", "法拉利",
    "车载音乐", "成立子公司", "薪资", "福利",
]

CATEGORY_KEYWORDS = [
    ("宏观新闻", ["中汽协", "乘联会", "工业和信息化部", "国家", "国务院", "市场监管总局", "海关总署", "销量", "产销", "进口", "出口"]),
    ("政策新闻", ["补贴", "政策", "消费券", "置换更新", "以旧换新", "地方", "商务局", "发改委"]),
    ("领导人动态", ["董事长", "总裁", "CEO", "高管", "任命", "出任", "离任", "辞任"]),
    ("产品信息", ["上市", "预售", "预订", "售价", "车型", "新车", "配置", "改款", "官图"]),
    ("技术新闻", ["技术", "专利", "电池", "智驾", "智能驾驶", "平台", "芯片", "架构"]),
    ("海外动态", ["海外", "欧洲", "美国", "德国", "泰国", "印尼", "墨西哥", "巴西", "出口", "全球"]),
    ("营销动态", ["发布会", "代言", "活动", "赛事", "联名", "营销"]),
    ("企业动态", ["公司", "集团", "工厂", "基地", "交付", "产能", "战略", "人事"]),
    ("品牌动态", ["品牌", "汽车"]),
]


def retry_sleep(attempt: int, base_sleep: float = 1.5):
    time.sleep(base_sleep * (attempt + 1))


def classify_error(message: str, content: str = "", status_code: Optional[int] = None, final_url: str = "") -> str:
    text = f"{message or ''}\n{content or ''}\n{final_url or ''}".lower()
    if status_code in (401, 403):
        return "blocked"
    if status_code == 404:
        return "not_found"
    if "captcha" in text or "安全验证" in text or "验证码" in text:
        return "captcha"
    if "visitor" in text or "login" in text or "请先登录" in text or "登录" in text:
        return "login_required"
    if "enable javascript" in text or "javascript" in text or "js" in text:
        return "js_required"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "empty" in text or "空" in text:
        return "empty_content"
    return "network_error" if message else "unknown"


def clean_text(text: str) -> str:
    if not text:
        return ""
    noise_patterns = [
        r"分享到.*", r"分享至.*", r"打开微信.*", r"扫描二维码.*", r"责任编辑[:：].*",
        r"免责声明[:：].*", r"版权声明[:：].*", r"版权所有.*", r"登录后.*", r"请先登录.*",
        r"点击查看更多.*", r"展开全文.*", r"收起全文.*", r"返回搜狐.*", r"广告.*",
    ]
    useless = {"确定", "取消", "登录", "注册", "更多", "收起", "展开", "分享", "评论", "点赞", "收藏", "首页", "返回顶部"}
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line in useless:
            continue
        if any(re.search(pattern, line) for pattern in noise_patterns):
            continue
        lines.append(line)
    cleaned, last = [], None
    for line in lines:
        if line != last:
            cleaned.append(line)
        last = line
    return "\n".join(cleaned).strip()


def parse_date_from_text(text: str, default_year: Optional[int] = None) -> Optional[str]:
    if not text:
        return None
    now_year = default_year or datetime.datetime.now().year
    patterns = [
        r"(?P<y>20\d{2})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日",
        r"(?P<y>20\d{2})[-/\.](?P<m>\d{1,2})[-/\.](?P<d>\d{1,2})",
        r"(?P<m>\d{1,2})月(?P<d>\d{1,2})日",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            gd = match.groupdict()
            y = int(gd.get("y") or now_year)
            m = int(gd["m"])
            d = int(gd["d"])
            try:
                return datetime.date(y, m, d).isoformat()
            except Exception:
                return None
    return None


def date_evidence(text: str, target_date: Optional[str] = None) -> Dict[str, Any]:
    parsed = parse_date_from_text(text)
    confidence = "none"
    if parsed:
        confidence = "high" if re.search(r"20\d{2}", text) else "medium"
    if target_date and parsed == target_date:
        confidence = "high"
    return {"date": parsed, "date_confidence": confidence, "date_evidence": text[:180] if parsed else ""}


def parse_cookie_string(cookie_string: Optional[str], domain: str) -> List[Dict[str, Any]]:
    if not cookie_string:
        return []
    cookies = []
    for pair in cookie_string.split(";"):
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name, value = name.strip(), value.strip()
        if name:
            cookies.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return cookies


def is_excluded(item: Dict[str, Any]) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('content', '')}"
    return any(p.lower() in text.lower() for p in EXCLUDE_PATTERNS)


def infer_category(title: str, summary: str = "") -> str:
    text = f"{title} {summary}"
    for category, kws in CATEGORY_KEYWORDS:
        if any(kw.lower() in text.lower() for kw in kws):
            return category
    return "其他新闻"


def normalize_title(title: str) -> str:
    title = re.sub(r"[\s\W_]+", "", title or "")
    return title[:80].lower()


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen_urls = set()
    for item in items:
        if is_excluded(item):
            continue
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        summary = (item.get("summary") or "").strip()
        if not title and not summary:
            continue
        if url and url in seen_urls:
            continue
        duplicate_idx = None
        for idx, old in enumerate(result):
            if title and old.get("title") and similar(title, old.get("title", "")) >= 0.86:
                duplicate_idx = idx
                break
        if duplicate_idx is not None:
            old = result[duplicate_idx]
            if len(summary) > len(old.get("summary", "")) or (url and not old.get("url")):
                merged_sources = old.get("merged_sources", []) + [{"source": old.get("source"), "url": old.get("url")}]
                item["merged_sources"] = merged_sources
                result[duplicate_idx] = item
            continue
        if url:
            seen_urls.add(url)
        result.append(item)
    return result


def filter_items_by_date(items: List[Dict[str, Any]], start_date: Optional[str], end_date: Optional[str], strict: bool = True) -> List[Dict[str, Any]]:
    if not start_date and not end_date:
        return items
    filtered = []
    start = datetime.date.fromisoformat(start_date) if start_date else None
    end = datetime.date.fromisoformat(end_date) if end_date else None
    target_year = start.year if start else (end.year if end else None)
    for item in items:
        text = " ".join(str(item.get(k, "")) for k in ["title", "summary", "content"])
        item_date = item.get("date") or parse_date_from_text(text, default_year=target_year)
        if item_date:
            item["date"] = item_date
        evidence = date_evidence(text, start_date if start_date == end_date else None)
        item.setdefault("date_confidence", evidence["date_confidence"])
        if not item_date:
            if not strict:
                filtered.append(item)
            continue
        try:
            d = datetime.date.fromisoformat(item_date)
            if start and d < start:
                continue
            if end and d > end:
                continue
            filtered.append(item)
        except Exception:
            if not strict:
                filtered.append(item)
    return filtered


TABLE_COLUMNS = ["信源来源", "交付日期", "热榜内容", "热点内容分类", "摘要（如热榜内容过于概要，则摘要补充）", "链接"]


CATEGORY_LABEL_MAP = {
    "宏观新闻": "宏观类",
    "政策新闻": "政策类",
    "行业新闻": "行业类",
    "企业动态": "企业类",
    "品牌动态": "品牌类",
    "领导人动态": "领导人类",
    "产品信息": "产品类",
    "营销动态": "营销类",
    "技术新闻": "技术类",
    "海外动态": "海外类",
    "其他新闻": "其他类",
}


def normalize_table_category(category: str) -> str:
    if not category:
        return "其他类"
    if category.endswith("类"):
        return category
    return CATEGORY_LABEL_MAP.get(category, category.replace("新闻", "类").replace("动态", "类").replace("信息", "类"))


def format_delivery_date(date_str: Optional[str]) -> str:
    if not date_str:
        return ""
    try:
        d = datetime.date.fromisoformat(str(date_str)[:10])
        return f"{d.isoformat()} 00:00:00"
    except Exception:
        return str(date_str)


NEWS_DIGEST_PATTERNS = [
    "汽车早报", "早报", "晚报", "日报", "周报", "一周", "快讯汇总", "新车预告", "第", "将上市", "盘点", "汇总", "多条", "要闻",
]


OFFICIAL_DOMAINS = [
    "miit.gov.cn", "samr.gov.cn", "mofcom.gov.cn", "ndrc.gov.cn", "gov.cn", "customs.gov.cn",
    "mps.gov.cn", "mot.gov.cn", "mee.gov.cn", "caam.org.cn", "cpcaauto.com",
]


VERTICAL_MEDIA_DOMAINS = ["yiche.com", "autohome.com.cn", "dongchedi.com", "pcauto.com.cn", "xcar.com.cn"]


def is_news_digest_link(item: Dict[str, Any]) -> bool:
    title = item.get("title", "") or ""
    source = item.get("source", "") or ""
    source_type = item.get("source_type", "") or ""
    url = item.get("url", "") or ""
    if source_type in {"cls_auto_morning", "jiemian_auto_morning"}:
        return True
    if any(k in source for k in ["早报", "快讯汇总"]):
        return True
    # 汽车之家“第X周新车预告/汇总”这类不是单条原始新闻，不能作为最终链接
    if any(k in title for k in ["新车预告", "将上市热门新车", "汇总", "盘点"]):
        return True
    if "cls.cn/detail" in url and "汽车早报" in source:
        return True
    if "jiemian.com/article" in url and "汽车早报" in source:
        return True
    return False


def classify_link_priority(url: str, title: str = "", category: str = "") -> Tuple[int, str]:
    url_l = (url or "").lower()
    if not url_l:
        return (99, "missing")
    if "mp.weixin.qq.com" in url_l:
        return (1, "wechat_official_account")
    if "weibo.com" in url_l:
        return (2, "official_weibo")
    if any(d in url_l for d in OFFICIAL_DOMAINS):
        return (1 if category in {"宏观新闻", "宏观类", "政策新闻", "政策类"} else 3, "official_site")
    if any(d in url_l for d in VERTICAL_MEDIA_DOMAINS):
        return (3, "vertical_media")
    if "finance.sina" in url_l or "sina.com.cn/finance" in url_l:
        return (4, "sina_finance")
    return (8, "other")


def infer_original_link_search_queries(item: Dict[str, Any]) -> List[str]:
    title = item.get("title", "") or ""
    category = item.get("category", "") or infer_category(title, item.get("summary", ""))
    queries: List[str] = []
    if category in {"宏观新闻", "政策新闻", "宏观类", "政策类"}:
        if any(k in title for k in ["工业和信息化部", "工信部"]):
            queries.append(f"site:miit.gov.cn {title}")
        if "市场监管" in title:
            queries.append(f"site:samr.gov.cn {title}")
        if "商务" in title or "以旧换新" in title or "消费" in title:
            queries.append(f"site:mofcom.gov.cn {title}")
        queries.append(f"政府 官网 {title}")
    if category in {"产品信息", "产品类", "营销动态", "营销类"} or is_new_car_post(title):
        queries.extend([f"{title} 官方公众号", f"{title} 官方微博", f"site:mp.weixin.qq.com {title}", f"site:weibo.com {title}"])
    queries.extend([f"site:yiche.com {title}", f"site:autohome.com.cn {title}", f"site:dongchedi.com {title}", f"site:finance.sina.com.cn {title}"])
    seen, unique = set(), []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            unique.append(q)
    return unique[:10]


def resolve_original_link(item: Dict[str, Any], search_if_needed: bool = False, limit: int = 6) -> Dict[str, Any]:
    title = item.get("title", "") or ""
    category = item.get("category", "") or infer_category(title, item.get("summary", ""))
    candidates: List[Dict[str, Any]] = []
    for key in ["original_url", "official_url", "url"]:
        url = item.get(key)
        if not url:
            continue
        priority, link_type = classify_link_priority(url, title, category)
        if key == "url" and is_news_digest_link(item):
            priority = 90
            link_type = "news_digest_not_allowed"
        candidates.append({"url": url, "priority": priority, "type": link_type, "source": key})
    search_attempts: List[Dict[str, Any]] = []
    if search_if_needed and (not candidates or min(c["priority"] for c in candidates) >= 8):
        for query in infer_original_link_search_queries(item):
            sr = web_search(query, limit=limit, retries=1)
            search_attempts.append({"query": query, "ok": sr.get("ok"), "count": len(sr.get("results", []))})
            for r in sr.get("results", []):
                url = r.get("url", "")
                text = f"{r.get('title','')} {r.get('snippet','')}"
                if not url or not text:
                    continue
                priority, link_type = classify_link_priority(url, title, category)
                if priority <= 4:
                    candidates.append({"url": url, "priority": priority, "type": link_type, "source": "search", "query": query})
            if candidates and min(c["priority"] for c in candidates) <= 2:
                break
    candidates = sorted(candidates, key=lambda x: x.get("priority", 99))
    best = candidates[0] if candidates else {"url": "", "priority": 99, "type": "missing"}
    return {"url": best.get("url", ""), "link_type": best.get("type", "missing"), "priority": best.get("priority", 99), "is_original_link": best.get("priority", 99) <= 4, "candidates": candidates[:8], "search_attempts": search_attempts}


def item_to_table_row(item: Dict[str, Any], default_date: Optional[str] = None, resolve_links: bool = False) -> Dict[str, str]:
    title = clean_text(item.get("title") or item.get("summary") or "").splitlines()[0] if (item.get("title") or item.get("summary")) else ""
    summary = clean_text(item.get("summary") or item.get("content") or title)
    category = item.get("category") or infer_category(title, summary)
    link_info = resolve_original_link(item, search_if_needed=resolve_links)
    item["original_url"] = link_info.get("url", "")
    item["original_link_type"] = link_info.get("link_type", "")
    item["is_original_link"] = link_info.get("is_original_link", False)
    return {
        "信源来源": item.get("source", ""),
        "交付日期": format_delivery_date(item.get("date") or default_date),
        "热榜内容": title,
        "热点内容分类": normalize_table_category(category),
        "摘要（如热榜内容过于概要，则摘要补充）": summary,
        "链接": link_info.get("url", "") if link_info.get("is_original_link") else "",
    }


def items_to_table_rows(items: List[Dict[str, Any]], default_date: Optional[str] = None, resolve_links: bool = False) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for item in items:
        row = item_to_table_row(item, default_date=default_date, resolve_links=resolve_links)
        # 禁止拼盘类信源作为链接；若未解析到原始链接，链接列留空，供后续补链流程处理。
        if row.get("热榜内容"):
            rows.append(row)
    return rows


@mcp.tool()
def resolve_news_original_links(items: List[Dict[str, Any]], default_date: Optional[str] = None, limit: int = 6) -> dict:
    """为已采集新闻按优先级补原始链接：公众号 > 官方微博 > 官网/垂媒 > 新浪财经；拼盘信源不作为最终链接。"""
    enriched: List[Dict[str, Any]] = []
    for item in items:
        row = item_to_table_row(item, default_date=default_date, resolve_links=True)
        enriched.append({"item": item, "table_row": row, "original_url": item.get("original_url", ""), "original_link_type": item.get("original_link_type", ""), "is_original_link": item.get("is_original_link", False)})
    rows = [x["table_row"] for x in enriched]
    missing = sum(1 for r in rows if not r.get("链接"))
    return {"ok": True, "count": len(rows), "missing_original_link_count": missing, "original_link_count": len(rows) - missing, "table_columns": TABLE_COLUMNS, "table_rows": rows, "items": [x["item"] for x in enriched]}


def build_table_workbook_payload(rows: List[Dict[str, str]], sheet_name: str = "行业快讯") -> Dict[str, Any]:
    return {
        "sheets": [{
            "name": sheet_name,
            "columns": [
                {"key": "source", "header": "信源来源", "width": 18},
                {"key": "date", "header": "交付日期", "width": 20},
                {"key": "title", "header": "热榜内容", "width": 42},
                {"key": "category", "header": "热点内容分类", "width": 16},
                {"key": "summary", "header": "摘要（如热榜内容过于概要，则摘要补充）", "width": 80},
                {"key": "url", "header": "链接", "width": 45},
            ],
            "rows": [
                {
                    "source": r.get("信源来源", ""),
                    "date": r.get("交付日期", ""),
                    "title": r.get("热榜内容", ""),
                    "category": r.get("热点内容分类", ""),
                    "summary": r.get("摘要（如热榜内容过于概要，则摘要补充）", ""),
                    "url": r.get("链接", ""),
                }
                for r in rows
            ],
            "freeze_panes": "A2",
            "auto_filter": True,
        }]
    }


def extract_items_by_lines(content: str, source_url: str = "", source_name: str = "") -> List[Dict[str, Any]]:
    if not content:
        return []
    items = []
    for line in content.splitlines():
        line = line.strip(" \t-•|，,。")
        if len(line) < 10 or line in {"首页", "新闻", "汽车", "财经", "科技", "更多", "登录", "注册"}:
            continue
        if len(line) > 220:
            line = line[:220]
        items.append({
            "title": line[:90],
            "summary": line,
            "url": source_url,
            "source": source_name,
            "date": parse_date_from_text(line),
            "category": infer_category(line),
            "confidence": "medium" if parse_date_from_text(line) else "low",
        })
    return items


def extract_links_from_html(html: str, base_url: str = "") -> List[Dict[str, str]]:
    soup = BeautifulSoup(html or "", "lxml")
    links = []
    for a in soup.find_all("a"):
        title = a.get_text(" ", strip=True)
        href = a.get("href", "")
        if not title or not href or href.startswith("javascript"):
            continue
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/") and base_url.startswith("http"):
            m = re.match(r"https?://[^/]+", base_url)
            if m:
                href = m.group(0) + href
        links.append({"title": title, "url": href})
    return links


def requests_get(url: str, timeout: int, headers: Optional[Dict[str, str]] = None) -> requests.Response:
    return requests.get(url, headers=headers or DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)


def fetch_static_once(url: str, timeout: int, extract_text: bool, clean: bool, headers: Optional[Dict[str, str]] = None) -> dict:
    resp = requests_get(url, timeout, headers=headers)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    html = resp.text
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    content = ""
    if extract_text:
        extracted = trafilatura.extract(html, include_comments=False, include_tables=True, include_links=False)
        if extracted:
            content = extracted.strip()
        else:
            for tag in soup(["script", "style", "noscript", "svg"]):
                tag.decompose()
            content = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    if clean:
        content = clean_text(content)
    detected_issue = None
    if not content or len(content) < 20:
        detected_issue = "empty_content"
    if "Please Enable JavaScript" in html or "enable javascript" in html.lower():
        detected_issue = "js_required"
    if "安全验证" in html or "captcha" in html.lower():
        detected_issue = "captcha"
    if "Sina Visitor System" in title or "visitor" in resp.url.lower():
        detected_issue = "login_required"
    return {
        "ok": True,
        "url": resp.url,
        "title": title,
        "content": content,
        "date": parse_date_from_text(title + "\n" + content),
        "status_code": resp.status_code,
        "content_length": len(content),
        "detected_issue": detected_issue,
        "links": extract_links_from_html(html, resp.url)[:50],
    }


def run_coro_sync(coro):
    """Run async code from both plain sync and already-running event-loop environments."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    if nest_asyncio is not None:
        return loop.run_until_complete(coro)

    result_box: Dict[str, Any] = {}
    error_box: Dict[str, BaseException] = {}

    def runner():
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            result_box["value"] = new_loop.run_until_complete(coro)
        except BaseException as exc:
            error_box["error"] = exc
        finally:
            new_loop.close()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


def parse_baidu_results(html: str, keyword: str, limit: int) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html or "", "lxml")
    results = []
    for block in soup.select(".result, .c-container, h3"):
        a = block.find("a") if hasattr(block, "find") else None
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        url = a.get("href", "")
        snippet = block.get_text(" ", strip=True)
        if title and url:
            evidence = date_evidence(title + " " + snippet)
            results.append({"title": title, "snippet": snippet[:300], "url": url, "source": "baidu_fallback", **evidence})
        if len(results) >= limit:
            break
    return results


def fallback_search(keyword: str, limit: int, timeout: int = 15) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    attempts = []
    results: List[Dict[str, Any]] = []
    search_urls = [
        f"https://www.baidu.com/s?wd={quote_plus(keyword)}",
        f"https://www.so.com/s?q={quote_plus(keyword)}",
    ]
    for search_url in search_urls:
        try:
            r = requests_get(search_url, timeout, headers=DEFAULT_HEADERS)
            r.raise_for_status()
            issue = classify_error("", r.text, r.status_code, r.url)
            attempts.append({"url": search_url, "final_url": r.url, "status_code": r.status_code, "detected_issue": issue if issue != "unknown" else None})
            parsed = parse_baidu_results(r.text, keyword, limit)
            if parsed:
                results.extend(parsed)
                break
        except Exception as exc:
            attempts.append({"url": search_url, "ok": False, "error_type": classify_error(str(exc)), "message": str(exc)})
    return results[:limit], attempts


@mcp.tool()
def web_search(keyword: str, limit: int = 10, retries: int = 2) -> dict:
    """根据关键词搜索互联网内容，返回标题、摘要、URL。优先 Bing API，缺少 KEY 时使用搜索页兜底。"""
    api_key = os.getenv("BING_SEARCH_API_KEY")
    results: List[Dict[str, Any]] = []
    attempts: List[Dict[str, Any]] = []
    if api_key:
        endpoint = "https://api.bing.microsoft.com/v7.0/search"
        params = {
            "q": keyword,
            "count": min(limit, 50),
            "mkt": "zh-CN",
            "setLang": "zh-Hans",
            "responseFilter": "Webpages",
            "textDecorations": False,
            "textFormat": "Raw",
        }
        headers = {"Ocp-Apim-Subscription-Key": api_key}
        last_error = None
        for attempt in range(retries + 1):
            try:
                resp = requests.get(endpoint, headers=headers, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                webpages = data.get("webPages", {}).get("value", [])
                for item in webpages[:limit]:
                    text = item.get("name", "") + " " + item.get("snippet", "")
                    results.append({
                        "title": item.get("name", ""),
                        "snippet": item.get("snippet", ""),
                        "url": item.get("url", ""),
                        "source": "bing",
                        **date_evidence(text),
                    })
                return {"ok": True, "keyword": keyword, "results": results, "attempts": [{"source": "bing", "ok": True}]}
            except Exception as e:
                last_error = str(e)
                attempts.append({"source": "bing", "ok": False, "error_type": classify_error(last_error), "message": last_error})
                if attempt < retries:
                    retry_sleep(attempt)
    else:
        attempts.append({"source": "bing", "ok": False, "error_type": "missing_api_key", "message": "BING_SEARCH_API_KEY is not configured; using fallback search pages"})

    fallback_results, fallback_attempts = fallback_search(keyword, limit)
    attempts.extend(fallback_attempts)
    results.extend(fallback_results)
    return {
        "ok": bool(results),
        "keyword": keyword,
        "results": dedupe_items(results),
        "attempts": attempts,
        "message": "fallback search used" if results else "Error: no search results from API or fallback pages",
    }


@mcp.tool()
def web_fetch(url: str, timeout: int = 15, extract_text: bool = True, retries: int = 2, clean: bool = True) -> dict:
    """抓取静态网页内容并提取正文，返回失败类型、正文长度和页面链接线索。"""
    last_error = None
    last_issue = None
    for attempt in range(retries + 1):
        try:
            result = fetch_static_once(url, timeout, extract_text, clean, headers=DEFAULT_HEADERS)
            if result.get("detected_issue") in {"js_required", "empty_content"}:
                try:
                    mobile = fetch_static_once(url, timeout, extract_text, clean, headers=MOBILE_HEADERS)
                    if mobile.get("content_length", 0) > result.get("content_length", 0):
                        mobile["fallback_used"] = "mobile_user_agent"
                        return mobile
                except Exception:
                    pass
            return result
        except Exception as e:
            last_error = str(e)
            last_issue = classify_error(last_error)
            if attempt < retries:
                retry_sleep(attempt)
    return {"ok": False, "url": url, "error_type": last_issue, "message": f"Error: {last_error}"}


async def _dynamic_once(url: str, timeout: int, wait_until: str, wait_selector: Optional[str], scroll: bool, scroll_times: int, extract_text: bool, headless: bool, cookie_string: Optional[str], cookie_domain: Optional[str], clean: bool, capture_network: bool = True) -> dict:
    browser = None
    network: List[Dict[str, Any]] = []
    console_errors: List[str] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(user_agent=DEFAULT_HEADERS["User-Agent"], locale="zh-CN", viewport={"width": 1440, "height": 1200})
            if cookie_string and cookie_domain:
                cookies = parse_cookie_string(cookie_string, cookie_domain)
                if cookies:
                    await context.add_cookies(cookies)
            page = await context.new_page()
            if capture_network:
                page.on("response", lambda resp: network.append({"url": resp.url, "status": resp.status, "content_type": resp.headers.get("content-type", "")}) if ("json" in resp.headers.get("content-type", "") or "api" in resp.url.lower()) else None)
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            response = await page.goto(url, wait_until=wait_until, timeout=timeout)
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=timeout)
            await page.wait_for_timeout(2000)
            if scroll:
                for _ in range(scroll_times):
                    await page.mouse.wheel(0, 1400)
                    await page.wait_for_timeout(1200)
            title = await page.title()
            html = await page.content()
            final_url = page.url
            status_code = response.status if response else None
            content = ""
            if extract_text:
                extracted = trafilatura.extract(html, include_comments=False, include_tables=True, include_links=False)
                if extracted:
                    content = extracted.strip()
                else:
                    soup = BeautifulSoup(html, "lxml")
                    for tag in soup(["script", "style", "noscript", "svg"]):
                        tag.decompose()
                    content = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
            if clean:
                content = clean_text(content)
            issue = None
            if not content or len(content) < 20:
                issue = "empty_content"
            issue = classify_error("", title + "\n" + content, status_code, final_url) if not issue else issue
            await context.close()
            await browser.close()
            return {
                "ok": True,
                "url": final_url,
                "title": title,
                "content": content,
                "date": parse_date_from_text(title + "\n" + content),
                "status_code": status_code,
                "content_length": len(content),
                "detected_issue": issue if issue != "unknown" else None,
                "network_json_candidates": network[:40],
                "console_errors": console_errors[:10],
            }
    except Exception as e:
        if browser:
            await browser.close()
        return {"ok": False, "url": url, "error_type": classify_error(str(e)), "message": f"Error: {str(e)}", "network_json_candidates": network[:40], "console_errors": console_errors[:10]}


@mcp.tool()
def web_fetch_dynamic(url: str, timeout: int = 30000, wait_until: str = "networkidle", wait_selector: Optional[str] = None, scroll: bool = True, scroll_times: int = 3, extract_text: bool = True, headless: bool = True, cookie_string: Optional[str] = None, cookie_domain: Optional[str] = None, retries: int = 2, clean: bool = True) -> dict:
    """使用浏览器渲染抓取动态网页内容；兼容已有事件循环，支持网络 JSON 候选记录。"""
    last_result = None
    for attempt in range(retries + 1):
        result = run_coro_sync(_dynamic_once(url, timeout, wait_until, wait_selector, scroll, scroll_times, extract_text, headless, cookie_string, cookie_domain, clean))
        last_result = result
        if result.get("ok") and result.get("detected_issue") not in {"empty_content", "js_required"}:
            return result
        if attempt < retries:
            retry_sleep(attempt)
    return last_result or {"ok": False, "url": url, "error_type": "unknown", "message": "Error: unknown dynamic fetch error"}


def build_source_status(name: str, source_type: str, result: dict, count: int) -> Dict[str, Any]:
    return {
        "name": name,
        "ok": bool(result.get("ok")) and count > 0,
        "fetch_ok": bool(result.get("ok")),
        "type": source_type,
        "count": count,
        "message": result.get("message", ""),
        "error_type": result.get("error_type") or result.get("detected_issue"),
        "final_url": result.get("url"),
        "status_code": result.get("status_code"),
        "content_length": result.get("content_length"),
    }


def enrich_item(item: Dict[str, Any], source_name: str, source_type: str, target_date: Optional[str] = None) -> Dict[str, Any]:
    item.setdefault("source", source_name)
    item.setdefault("source_type", source_type)
    item.setdefault("summary", item.get("snippet", ""))
    item.setdefault("category", infer_category(item.get("title", ""), item.get("summary", "")))
    ev = date_evidence(f"{item.get('title','')} {item.get('summary','')} {item.get('content','')}", target_date)
    item.setdefault("date", ev["date"])
    item.setdefault("date_confidence", ev["date_confidence"])
    item.setdefault("confidence", "high" if item.get("url") and item.get("date_confidence") == "high" else "medium" if item.get("url") else "low")
    return item


def cls_title_date(title: str, publish_date: Optional[str] = None) -> Optional[str]:
    """Parse 财联社汽车早报【M月D日】 date, using publish year when available."""
    if not title:
        return None
    year = None
    if publish_date:
        try:
            year = datetime.date.fromisoformat(publish_date).year
        except Exception:
            year = None
    year = year or datetime.datetime.now().year
    m = re.search(r"汽车早报【(?P<m>\d{1,2})月(?P<d>\d{1,2})日】", title)
    if not m:
        return parse_date_from_text(title, default_year=year)
    try:
        return datetime.date(year, int(m.group("m")), int(m.group("d"))).isoformat()
    except Exception:
        return None


def discover_cls_auto_morning_links(timeout: int = 20, retries: int = 1) -> dict:
    """Fetch 财联社汽车早报专题页 and return date -> detail link mapping."""
    subject_url = "https://www.cls.cn/subject/7527"
    result = web_fetch(subject_url, timeout=timeout, retries=retries, clean=False)
    if not result.get("ok"):
        return {"ok": False, "url": subject_url, "message": result.get("message", ""), "error_type": result.get("error_type")}
    links_by_date: Dict[str, Dict[str, Any]] = {}
    content = result.get("content", "")
    lines = content.splitlines()
    current_publish_date = None
    for line in lines:
        line = line.strip()
        pub = re.search(r"(20\d{2}-\d{1,2}-\d{1,2})\s+\d{1,2}:\d{2}", line)
        if pub:
            try:
                current_publish_date = datetime.date.fromisoformat(pub.group(1)).isoformat()
            except Exception:
                current_publish_date = pub.group(1)
        if "财联社汽车早报【" in line:
            item_date = cls_title_date(line, current_publish_date)
            if item_date:
                links_by_date.setdefault(item_date, {"title": line, "date": item_date, "publish_date": current_publish_date, "url": ""})
    for link in result.get("links", []):
        title = link.get("title", "")
        if "财联社汽车早报【" not in title:
            continue
        item_date = cls_title_date(title)
        if item_date:
            links_by_date.setdefault(item_date, {"title": title, "date": item_date, "publish_date": None, "url": ""})
            links_by_date[item_date]["url"] = link.get("url", "")
            links_by_date[item_date]["title"] = title
    return {"ok": True, "url": result.get("url", subject_url), "count": len(links_by_date), "links_by_date": links_by_date, "content_length": result.get("content_length")}


def split_cls_auto_morning_detail(content: str) -> List[Dict[str, str]]:
    """Split a 财联社汽车早报 detail article into individual news items."""
    if not content:
        return []
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    stop_prefixes = ("财联社汽车：", "（财联社记者", "关于我们", "网站声明")
    items: List[Dict[str, str]] = []
    current_title = None
    current_body: List[str] = []

    def flush():
        nonlocal current_title, current_body
        if current_title:
            summary = "\n".join(current_body).strip()
            if summary.startswith(current_title):
                summary = summary[len(current_title):].strip()
            items.append({"title": current_title, "summary": summary or current_title})
        current_title, current_body = None, []

    for line in lines:
        if line.startswith(stop_prefixes):
            continue
        if re.match(r"^[①②③④⑤⑥⑦⑧⑨⑩]", line):
            continue
        if len(line) <= 4:
            continue
        # 财联社详情页中，每条新闻标题通常独占一行，后面跟正文。
        is_heading = (
            len(line) <= 80
            and not line.startswith(("7月", "据", "根据", "此前", "其中", "动力方面", "财报显示"))
            and not line.endswith("。")
            and "财联社汽车早报" not in line
        )
        if is_heading:
            flush()
            current_title = line
        elif current_title:
            current_body.append(line)
    flush()
    # 过滤非新闻导航残留
    return [i for i in items if len(i.get("title", "")) >= 6 and "财联社汽车早报" not in i.get("title", "")]


@mcp.tool()
def collect_cls_auto_morning(start_date: str, end_date: Optional[str] = None, timeout: int = 20, retries: int = 1) -> dict:
    """采集财联社汽车早报。支持单日或日期范围，先从专题页发现详情链接，再抓取详情页完整内容。"""
    end_date = end_date or start_date
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    discovered = discover_cls_auto_morning_links(timeout=timeout, retries=retries)
    if not discovered.get("ok"):
        return {"ok": False, "source": "财联社汽车早报", "start_date": start_date, "end_date": end_date, "count": 0, "items": [], "message": discovered.get("message", ""), "error_type": discovered.get("error_type")}
    links_by_date = discovered.get("links_by_date", {})
    all_items: List[Dict[str, Any]] = []
    missing_dates: List[str] = []
    detail_status: List[Dict[str, Any]] = []
    d = start
    while d <= end:
        ds = d.isoformat()
        link_info = links_by_date.get(ds)
        if not link_info or not link_info.get("url"):
            missing_dates.append(ds)
            d += datetime.timedelta(days=1)
            continue
        detail = web_fetch(link_info["url"], timeout=timeout, retries=retries, clean=False)
        detail_status.append({"date": ds, "url": link_info["url"], "ok": detail.get("ok"), "content_length": detail.get("content_length"), "message": detail.get("message", "")})
        if detail.get("ok"):
            parts = split_cls_auto_morning_detail(detail.get("content", ""))
            for part in parts:
                all_items.append({
                    "title": part["title"],
                    "summary": clean_text(part.get("summary", "")),
                    "content": part.get("summary", ""),
                    "url": link_info["url"],
                    "source": "财联社汽车早报",
                    "source_type": "cls_auto_morning",
                    "date": ds,
                    "publish_date": link_info.get("publish_date") or detail.get("date") or ds,
                    "category": infer_category(part["title"], part.get("summary", "")),
                    "confidence": "high",
                    "date_confidence": "high",
                })
        d += datetime.timedelta(days=1)
    return {
        "ok": True,
        "source": "财联社汽车早报",
        "start_date": start_date,
        "end_date": end_date,
        "subject_url": discovered.get("url"),
        "discovered_count": discovered.get("count", 0),
        "missing_dates": missing_dates,
        "detail_status": detail_status,
        "count": len(all_items),
        "items": all_items,
    }


def extract_jiemian_article_links(html: str, only_auto_morning: bool = False) -> List[str]:
    """Extract article URLs from 界面新闻 search/list HTML. Mobile list pages expose article links reliably."""
    urls: List[str] = []
    soup = BeautifulSoup(html or "", "lxml")
    for a in soup.find_all("a"):
        href = a.get("href", "")
        text = a.get_text(" ", strip=True)
        if not href:
            continue
        if only_auto_morning and "汽车早报" not in text:
            continue
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://m.jiemian.com" + href
        href = href.replace("https://m.jiemian.com/article/", "https://m.jiemian.com/article/")
        if re.search(r"jiemian\.com/article/\d+\.html", href):
            if href not in urls:
                urls.append(href)
    if not only_auto_morning:
        for m in re.finditer(r"https?://(?:www\.|m\.)jiemian\.com/article/\d+\.html|//(?:www\.|m\.)jiemian\.com/article/\d+\.html|/article/\d+\.html", html or ""):
            href = m.group(0)
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://m.jiemian.com" + href
            if href not in urls:
                urls.append(href)
    return urls


def extract_jiemian_publish_date(html: str, content: str = "") -> Optional[str]:
    """Prefer metadata publish date instead of first date mentioned in article body."""
    text = html or ""
    patterns = [
        r"property=[\"']article:published_time[\"'][^>]+content=[\"'](?P<dt>20\d{2}-\d{1,2}-\d{1,2})",
        r"name=[\"']publishdate[\"'][^>]+content=[\"'](?P<dt>20\d{2}-\d{1,2}-\d{1,2})",
        r"datePublished[\"']?\s*[:=]\s*[\"'](?P<dt>20\d{2}-\d{1,2}-\d{1,2})",
        r"publishTime[\"']?\s*[:=]\s*[\"'](?P<dt>20\d{2}-\d{1,2}-\d{1,2})",
        r"发布时间[:：]?\s*(?P<dt>20\d{2}-\d{1,2}-\d{1,2})",
        r"(?P<dt>20\d{2}-\d{1,2}-\d{1,2})\s+\d{1,2}:\d{2}",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            try:
                return datetime.date.fromisoformat(m.group("dt")).isoformat()
            except Exception:
                return m.group("dt")
    return None


def fetch_jiemian_detail(url: str, timeout: int = 20, retries: int = 1) -> dict:
    """Fetch 界面新闻 detail with raw HTML metadata and extracted body."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests_get(url, timeout, headers=DEFAULT_HEADERS)
            resp.raise_for_status()
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding
            html = resp.text
            soup = BeautifulSoup(html, "lxml")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            extracted = trafilatura.extract(html, include_comments=False, include_tables=True, include_links=False)
            if extracted:
                content = extracted.strip()
            else:
                for tag in soup(["script", "style", "noscript", "svg"]):
                    tag.decompose()
                content = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
            return {
                "ok": True,
                "url": resp.url,
                "title": title,
                "content": clean_text(content),
                "raw_content": content,
                "publish_date": extract_jiemian_publish_date(html, content),
                "status_code": resp.status_code,
                "content_length": len(content),
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries:
                retry_sleep(attempt)
    return {"ok": False, "url": url, "message": f"Error: {last_error}", "error_type": classify_error(last_error or "")}


def discover_jiemian_auto_morning_links(start_date: str, end_date: str, timeout: int = 20, retries: int = 1, max_candidates: int = 30) -> dict:
    """Use 界面新闻移动端汽车列表页 https://m.jiemian.com/lists/51_1.html to find 汽车早报 articles, then verify by detail publish date."""
    base_url = "https://m.jiemian.com/lists/51_{page}.html"
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    candidate_urls: List[str] = []
    list_status: List[Dict[str, Any]] = []
    max_pages = max(1, min(8, (max_candidates // 8) + 2))
    for page_no in range(1, max_pages + 1):
        list_url = base_url.format(page=page_no)
        last_error = None
        html = ""
        for attempt in range(retries + 1):
            try:
                resp = requests_get(list_url, timeout, headers=MOBILE_HEADERS)
                resp.raise_for_status()
                if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding
                html = resp.text
                break
            except Exception as exc:
                last_error = str(exc)
                if attempt < retries:
                    retry_sleep(attempt)
        if not html:
            list_status.append({"url": list_url, "ok": False, "message": last_error, "error_type": classify_error(last_error or "")})
            continue
        urls = extract_jiemian_article_links(html, only_auto_morning=True)
        list_status.append({"url": list_url, "ok": True, "count": len(urls)})
        for u in urls:
            if u not in candidate_urls:
                candidate_urls.append(u)
        if len(candidate_urls) >= max_candidates:
            break
    candidate_urls = candidate_urls[:max_candidates]
    links_by_date: Dict[str, Dict[str, Any]] = {}
    checked: List[Dict[str, Any]] = []
    for url in candidate_urls:
        detail = fetch_jiemian_detail(url, timeout=timeout, retries=0)
        title = detail.get("title", "")
        if detail.get("ok") and "汽车早报" in title:
            pub_date = detail.get("publish_date") or parse_date_from_text(detail.get("content", ""), default_year=start.year)
            checked.append({"url": url, "title": title, "publish_date": pub_date, "ok": True})
            if pub_date:
                try:
                    d = datetime.date.fromisoformat(pub_date)
                    if start <= d <= end:
                        links_by_date[pub_date] = {"title": title, "url": url, "date": pub_date, "publish_date": pub_date}
                except Exception:
                    pass
        else:
            checked.append({"url": url, "title": title, "publish_date": detail.get("publish_date"), "ok": bool(detail.get("ok")), "message": detail.get("message", "")})
    return {"ok": True, "url": base_url.format(page=1), "list_status": list_status, "candidate_count": len(candidate_urls), "checked": checked[:max_candidates], "links_by_date": links_by_date}


def split_jiemian_auto_morning_detail(content: str) -> List[Dict[str, str]]:
    """Split 界面新闻汽车早报 body into individual news items."""
    if not content:
        return []
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    items: List[Dict[str, str]] = []
    current_title = None
    current_body: List[str] = []
    skip_contains = ("界面新闻", "未经正式授权", "版权", "广告", "责任编辑")

    def flush():
        nonlocal current_title, current_body
        if current_title:
            summary = "\n".join(current_body).strip()
            items.append({"title": current_title, "summary": summary or current_title})
        current_title, current_body = None, []

    for line in lines:
        if any(s in line for s in skip_contains):
            continue
        if len(line) <= 4:
            continue
        is_heading = (
            len(line) <= 90
            and not line.endswith("。")
            and not line.startswith(("7月", "当地时间", "据", "根据", "该公司", "此次", "其中"))
            and "汽车早报" not in line
        )
        if is_heading:
            flush()
            current_title = line
        elif current_title:
            current_body.append(line)
    flush()
    return [i for i in items if len(i.get("title", "")) >= 6]


@mcp.tool()
def collect_jiemian_auto_morning(start_date: str, end_date: Optional[str] = None, timeout: int = 20, retries: int = 1, max_candidates: int = 30) -> dict:
    """采集界面新闻汽车早报。通过移动端汽车列表页发现对应日期文章，再抓取详情页并拆分条目。"""
    end_date = end_date or start_date
    discovered = discover_jiemian_auto_morning_links(start_date, end_date, timeout=timeout, retries=retries, max_candidates=max_candidates)
    if not discovered.get("ok"):
        return {"ok": False, "source": "界面新闻汽车早报", "start_date": start_date, "end_date": end_date, "count": 0, "items": [], "message": discovered.get("message", ""), "error_type": discovered.get("error_type")}
    links_by_date = discovered.get("links_by_date", {})
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    all_items: List[Dict[str, Any]] = []
    missing_dates: List[str] = []
    detail_status: List[Dict[str, Any]] = []
    d = start
    while d <= end:
        ds = d.isoformat()
        link_info = links_by_date.get(ds)
        if not link_info:
            missing_dates.append(ds)
            d += datetime.timedelta(days=1)
            continue
        detail = fetch_jiemian_detail(link_info["url"], timeout=timeout, retries=retries)
        detail_status.append({"date": ds, "url": link_info["url"], "ok": detail.get("ok"), "content_length": detail.get("content_length"), "message": detail.get("message", "")})
        if detail.get("ok"):
            parts = split_jiemian_auto_morning_detail(detail.get("content", "") or detail.get("raw_content", ""))
            for part in parts:
                all_items.append({
                    "title": part["title"],
                    "summary": clean_text(part.get("summary", "")),
                    "content": part.get("summary", ""),
                    "url": link_info["url"],
                    "source": "界面新闻汽车早报",
                    "source_type": "jiemian_auto_morning",
                    "date": ds,
                    "publish_date": detail.get("publish_date") or ds,
                    "category": infer_category(part["title"], part.get("summary", "")),
                    "confidence": "high",
                    "date_confidence": "high",
                })
        d += datetime.timedelta(days=1)
    return {
        "ok": True,
        "source": "界面新闻汽车早报",
        "start_date": start_date,
        "end_date": end_date,
        "search_url": discovered.get("url"),
        "candidate_count": discovered.get("candidate_count", 0),
        "missing_dates": missing_dates,
        "detail_status": detail_status,
        "checked": discovered.get("checked", []),
        "count": len(all_items),
        "items": all_items,
    }


def parse_json_or_jsonp(text: str) -> dict:
    """Parse JSON or JSONP response."""
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("{") or raw.startswith("["):
        return json.loads(raw)
    m = re.search(r"^[\w$]+\((.*)\)\s*;?$", raw, re.S)
    if m:
        return json.loads(m.group(1))
    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        return json.loads(raw[first:last + 1])
    return {}


@mcp.tool()
def collect_sina_auto_7x24(start_date: str, end_date: Optional[str] = None, pages: int = 12, limit: int = 20, timeout: int = 20, retries: int = 1) -> dict:
    """采集新浪汽车7x24快讯。来源：https://auto.sina.com.cn/7x24/?tagid=1。不传 day，连续翻页并按 cTime 过滤，避免 day=YYYY-MM-DD 返回空。"""
    end_date = end_date or start_date
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    api = "https://a.sina.cn/topic/inside/shortnews/getTagnews/"
    all_items: List[Dict[str, Any]] = []
    page_status: List[Dict[str, Any]] = []
    stop_early = False
    for page in range(1, max(1, pages) + 1):
        if stop_early:
            break
        params = {"day": "", "page": page, "tagid": 1, "limit": limit}
        last_error = None
        data = None
        final_url = api
        for attempt in range(retries + 1):
            try:
                resp = requests.get(api, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
                final_url = resp.url
                resp.raise_for_status()
                data = parse_json_or_jsonp(resp.text)
                break
            except Exception as exc:
                last_error = str(exc)
                if attempt < retries:
                    retry_sleep(attempt)
        if data is None:
            page_status.append({"page": page, "ok": False, "url": final_url, "message": f"Error: {last_error}", "error_type": classify_error(last_error or "")})
            continue
        rows = data.get("data") or []
        page_status.append({"page": page, "ok": True, "url": final_url, "count": len(rows), "code": data.get("code")})
        if not rows:
            break
        for row in rows:
            ctime = row.get("cTime") or ""
            item_date = parse_date_from_text(ctime)
            if not item_date:
                continue
            try:
                d = datetime.date.fromisoformat(item_date)
            except Exception:
                continue
            if d < start:
                stop_early = True
                continue
            if d > end:
                continue
            title = row.get("title") or ""
            summary = row.get("summary") or title
            url = row.get("URL") or row.get("wapURL") or "https://auto.sina.com.cn/7x24/?tagid=1"
            all_items.append({
                "title": title,
                "summary": clean_text(summary),
                "content": summary,
                "url": url,
                "wap_url": row.get("wapURL") or "",
                "source": "新浪汽车7x24快讯",
                "source_type": "sina_auto_7x24",
                "date": item_date,
                "publish_time": ctime,
                "category": infer_category(title, summary),
                "confidence": "high",
                "date_confidence": "high",
                "sina_id": row.get("id") or row.get("_id") or "",
                "channel": row.get("second") or row.get("newCar") or "",
                "sub_channel": row.get("third") or "",
                "image": row.get("pic") or "",
            })
    return {
        "ok": True,
        "source": "新浪汽车7x24快讯",
        "start_date": start_date,
        "end_date": end_date,
        "source_url": "https://auto.sina.com.cn/7x24/?tagid=1",
        "api_url": api,
        "page_status": page_status,
        "count": len(all_items),
        "items": dedupe_items(all_items),
    }


NEW_CAR_KEYWORDS = ["上市", "预售", "开启预售", "开启预订", "预订", "亮相", "发布", "售价", "价格", "权益价", "指导价"]


def split_weibo_posts(content: str) -> List[str]:
    """Best-effort split for rendered Weibo user page text."""
    if not content:
        return []
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    posts: List[str] = []
    current: List[str] = []
    noise = {"赞", "评论", "转发", "收藏", "微博", "首页", "发现", "超话", "消息", "登录", "注册"}
    for line in lines:
        if line in noise:
            continue
        # 微博渲染文本常以时间/日期或互动按钮作为边界；此处做保守聚合。
        if re.search(r"(20\d{2}[-年]\d{1,2}[-月]\d{1,2}|今天|昨天|\d{1,2}月\d{1,2}日)", line) and current:
            posts.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        posts.append("\n".join(current))
    # 若切分过碎/过少，回退为按空行或整页过滤
    if len(posts) <= 1:
        posts = ["\n".join(lines)]
    return [p.strip() for p in posts if len(p.strip()) >= 10]


def is_new_car_post(text: str) -> bool:
    if not text:
        return False
    if not any(k in text for k in NEW_CAR_KEYWORDS):
        return False
    # 排除纯互动/榜单/无车型信息内容
    if len(text) < 12:
        return False
    return True


def normalize_weibo_date(text: str, default_year: int) -> Optional[str]:
    d = parse_date_from_text(text, default_year=default_year)
    if d:
        return d
    return None


@mcp.tool()
def collect_yiche_weibo_new_cars(start_date: str, end_date: Optional[str] = None, weibo_cookie: Optional[str] = None, timeout: int = 60000, scroll_times: int = 8, retries: int = 1) -> dict:
    """采集易车微博指定日期范围内的新车上市/预售/预订/售价等信息。来源：https://weibo.com/u/1912222221。"""
    end_date = end_date or start_date
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    source_url = "https://weibo.com/u/1912222221"
    if not weibo_cookie:
        return {
            "ok": False,
            "source": "易车微博",
            "source_url": source_url,
            "start_date": start_date,
            "end_date": end_date,
            "count": 0,
            "items": [],
            "error_type": "login_required",
            "message": "Weibo usually requires login Cookie for stable collection. Pass weibo_cookie to collect_yiche_weibo_new_cars or collect_auto_5_sources.",
        }
    result = web_fetch_dynamic(
        url=source_url,
        timeout=timeout,
        wait_until="domcontentloaded",
        scroll=True,
        scroll_times=scroll_times,
        extract_text=True,
        headless=True,
        cookie_string=weibo_cookie,
        cookie_domain=".weibo.com",
        retries=retries,
        clean=True,
    )
    if not result.get("ok"):
        return {"ok": False, "source": "易车微博", "source_url": source_url, "start_date": start_date, "end_date": end_date, "count": 0, "items": [], "error_type": result.get("error_type"), "message": result.get("message", "")}
    content = result.get("content", "")
    if result.get("detected_issue") == "login_required" or "Sina Visitor System" in content:
        return {"ok": False, "source": "易车微博", "source_url": source_url, "start_date": start_date, "end_date": end_date, "count": 0, "items": [], "error_type": "login_required", "message": "Weibo visitor/login gate detected; valid weibo_cookie is required.", "content_length": result.get("content_length")}
    posts = split_weibo_posts(content)
    items: List[Dict[str, Any]] = []
    default_year = start.year
    for post in posts:
        if not is_new_car_post(post):
            continue
        post_date = normalize_weibo_date(post, default_year=default_year)
        if not post_date:
            # 微博列表可能只显示“今天/昨天”等相对时间，无法可靠映射时不作为高置信结果
            continue
        try:
            d = datetime.date.fromisoformat(post_date)
        except Exception:
            continue
        if d < start or d > end:
            continue
        # 用第一条含关键词的短句作为标题
        title = ""
        for line in post.splitlines():
            if is_new_car_post(line):
                title = line.strip()
                break
        title = title or post.splitlines()[0].strip()
        if len(title) > 90:
            title = title[:90]
        items.append({
            "title": title,
            "summary": clean_text(post),
            "content": post,
            "url": source_url,
            "source": "易车微博",
            "source_type": "yiche_weibo_new_cars",
            "date": post_date,
            "category": "产品信息",
            "confidence": "medium",
            "date_confidence": "medium",
        })
    return {
        "ok": True,
        "source": "易车微博",
        "source_url": source_url,
        "start_date": start_date,
        "end_date": end_date,
        "content_length": result.get("content_length"),
        "post_count": len(posts),
        "count": len(items),
        "items": dedupe_items(items),
    }


def recursive_find_policy_items(obj: Any, source_url: str, source_name: str = "中国汽车工业信息网") -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    title_keys = {"title", "name", "newsTitle", "articleTitle", "dataTitle", "headline"}
    date_keys = {"publishTime", "releaseTime", "createTime", "updateTime", "pubTime", "date", "time"}
    url_keys = {"url", "link", "href", "jumpUrl"}
    if isinstance(obj, dict):
        title = ""
        for k in title_keys:
            if isinstance(obj.get(k), str) and len(obj.get(k, "")) >= 6:
                title = obj.get(k, "")
                break
        if title and not any(p in title for p in ["首页", "登录", "注册", "栏目", "菜单"]):
            dt = ""
            for k in date_keys:
                if obj.get(k):
                    dt = str(obj.get(k))
                    break
            link = ""
            for k in url_keys:
                if obj.get(k):
                    link = str(obj.get(k))
                    break
            if link:
                link = normalize_article_url(source_url, link)
            item_date = parse_date_from_text(dt or title)
            items.append({
                "title": clean_text(title).splitlines()[0][:120],
                "summary": clean_text(title),
                "content": clean_text(json.dumps(obj, ensure_ascii=False)[:800]),
                "url": link or source_url,
                "source": source_name,
                "source_type": "autoinfo_policy_dynamic",
                "date": item_date,
                "category": "政策新闻" if "地方" in title or "补贴" in title else "宏观新闻",
                "confidence": "high" if item_date else "medium",
                "date_confidence": "high" if item_date else "none",
            })
        for v in obj.values():
            items.extend(recursive_find_policy_items(v, source_url, source_name))
    elif isinstance(obj, list):
        for v in obj:
            items.extend(recursive_find_policy_items(v, source_url, source_name))
    return items


def post_json(url: str, payload: Dict[str, Any], timeout: int = 20, retries: int = 1) -> Dict[str, Any]:
    headers = {**DEFAULT_HEADERS, "Content-Type": "application/json;charset=UTF-8", "Accept": "application/json, text/plain, */*", "Origin": "https://www.autoinfo.org.cn", "Referer": "https://www.autoinfo.org.cn/"}
    last_error = ""
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.encoding is None:
                resp.encoding = resp.apparent_encoding
            text = resp.text or ""
            try:
                data = resp.json()
            except Exception:
                data = parse_json_or_jsonp(text)
            return {"ok": resp.status_code < 400, "url": url, "status_code": resp.status_code, "payload": payload, "data": data, "text": text[:1000], "error_type": None if resp.status_code < 400 else classify_error("", text, resp.status_code)}
        except Exception as e:
            last_error = str(e)
            if attempt < retries:
                retry_sleep(attempt, 0.8)
    return {"ok": False, "url": url, "payload": payload, "message": last_error, "error_type": classify_error(last_error)}


def find_autoinfo_policy_menu_ids(menu_data: Any) -> List[Any]:
    ids: List[Any] = []
    def walk(obj: Any, path_text: str = ""):
        if isinstance(obj, dict):
            name = str(obj.get("name") or obj.get("title") or obj.get("menuName") or obj.get("label") or "")
            cur_path = f"{path_text} {name}"
            if any(k in cur_path for k in ["政策动态", "政策报道", "政策", "动态"]):
                for key in ["id", "menuId", "queryMenuId", "code", "value"]:
                    if obj.get(key) not in (None, ""):
                        ids.append(obj.get(key))
                        break
            for v in obj.values():
                walk(v, cur_path)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, path_text)
    walk(menu_data)
    seen, unique = set(), []
    for x in ids:
        sx = str(x)
        if sx not in seen:
            seen.add(sx)
            unique.append(x)
    return unique


def collect_autoinfo_policy_by_api(start_date: str, end_date: str, pages: int = 8, page_size: int = 10, timeout: int = 20, retries: int = 1) -> Dict[str, Any]:
    source_url = "https://www.autoinfo.org.cn/#/policy/dynamic/index"
    base = "https://www.autoinfo.org.cn/prod-api"
    attempts: List[Dict[str, Any]] = []
    menu_payloads = [{}, {"type": "policy"}, {"parentId": 0}, {"menuType": "policy"}]
    menu_ids: List[Any] = []
    for payload in menu_payloads:
        r = post_json(f"{base}/lo/findMenu", payload, timeout=timeout, retries=0)
        attempts.append({"api": "findMenu", "ok": r.get("ok"), "status_code": r.get("status_code"), "payload": payload, "error_type": r.get("error_type")})
        if r.get("ok"):
            menu_ids.extend(find_autoinfo_policy_menu_ids(r.get("data")))
    if not menu_ids:
        menu_ids = [None]
    items: List[Dict[str, Any]] = []
    list_api = f"{base}/api/newData/searchData/getDataQueryMenus"
    for menu_id in menu_ids[:8]:
        for page in range(1, pages + 1):
            payloads = [
                {"pageNum": page, "pageSize": page_size, "menuId": menu_id},
                {"pageNo": page, "pageSize": page_size, "menuId": menu_id},
                {"current": page, "size": page_size, "menuId": menu_id},
                {"pageNum": page, "pageSize": page_size, "queryMenuId": menu_id},
                {"pageNo": page, "pageSize": page_size, "queryMenuId": menu_id},
            ]
            if menu_id is None:
                payloads = [{k: v for k, v in p.items() if v is not None} for p in payloads]
            for payload in payloads:
                r = post_json(list_api, payload, timeout=timeout, retries=0)
                attempts.append({"api": "getDataQueryMenus", "ok": r.get("ok"), "status_code": r.get("status_code"), "payload": payload, "error_type": r.get("error_type")})
                if r.get("ok"):
                    found = recursive_find_policy_items(r.get("data"), source_url)
                    items.extend(found)
                    if found:
                        break
    items = filter_items_by_date(dedupe_items(items), start_date, end_date, strict=True)
    return {"ok": True, "source": "中国汽车工业信息网政策动态", "source_url": source_url, "method": "api_probe", "menu_ids": menu_ids, "attempts": attempts[:80], "count": len(items), "items": items}


async def _collect_autoinfo_policy_dynamic(start_date: str, end_date: str, timeout: int, max_pages: int) -> Dict[str, Any]:
    source_url = "https://www.autoinfo.org.cn/#/policy/dynamic/index"
    browser = None
    network_items: List[Dict[str, Any]] = []
    console_errors: List[str] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(user_agent=DEFAULT_HEADERS["User-Agent"], locale="zh-CN", viewport={"width": 1440, "height": 1200})
            page = await context.new_page()
            async def on_response(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct or "api" in resp.url.lower():
                        txt = await resp.text()
                        data = parse_json_or_jsonp(txt)
                        network_items.extend(recursive_find_policy_items(data, source_url))
                except Exception:
                    pass
            page.on("response", lambda resp: asyncio.create_task(on_response(resp)))
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            await page.goto(source_url, wait_until="domcontentloaded", timeout=timeout)
            await page.wait_for_timeout(3000)
            # 尽量切到“政策报道/政策动态”区域，并点击查看更多或分页
            for text in ["政策报道", "政策动态", "查看更多"]:
                try:
                    loc = page.get_by_text(text, exact=False).first
                    if await loc.count():
                        await loc.click(timeout=2000)
                        await page.wait_for_timeout(1500)
                except Exception:
                    pass
            snapshots: List[str] = []
            for _ in range(max_pages):
                try:
                    await page.mouse.wheel(0, 1600)
                    await page.wait_for_timeout(1200)
                    snapshots.append(await page.locator("body").inner_text(timeout=5000))
                except Exception:
                    pass
                clicked = False
                for text in ["查看更多", "加载更多", "下一页", ">"]:
                    try:
                        loc = page.get_by_text(text, exact=False).last
                        if await loc.count():
                            await loc.click(timeout=2000)
                            clicked = True
                            await page.wait_for_timeout(1800)
                            break
                    except Exception:
                        continue
                if not clicked:
                    # Element UI 分页 next 按钮兜底
                    try:
                        btn = page.locator(".btn-next, .el-pagination .btn-next, li.number").last
                        if await btn.count():
                            await btn.click(timeout=2000)
                            await page.wait_for_timeout(1800)
                        else:
                            break
                    except Exception:
                        break
            content = clean_text("\n".join(snapshots))
            text_items = extract_items_by_lines(content, source_url, "中国汽车工业信息网政策动态")
            items = network_items + text_items
            items = filter_items_by_date(dedupe_items(items), start_date, end_date, strict=True)
            await context.close()
            await browser.close()
            return {"ok": True, "source": "中国汽车工业信息网政策动态", "source_url": source_url, "method": "dynamic_click", "content_length": len(content), "network_item_count": len(network_items), "count": len(items), "items": items, "console_errors": console_errors[:10]}
    except Exception as e:
        if browser:
            await browser.close()
        return {"ok": False, "source": "中国汽车工业信息网政策动态", "source_url": source_url, "method": "dynamic_click", "count": 0, "items": [], "error_type": classify_error(str(e)), "message": f"Error: {str(e)}", "console_errors": console_errors[:10]}


@mcp.tool()
def collect_autoinfo_policy_dynamic(start_date: str, end_date: Optional[str] = None, pages: int = 8, page_size: int = 10, timeout: int = 60000, retries: int = 1) -> dict:
    """按日期采集中国汽车工业信息网“政策动态/政策报道”标题；支持接口探测、查看更多和翻页查找。"""
    end_date = end_date or start_date
    api_result = collect_autoinfo_policy_by_api(start_date, end_date, pages=pages, page_size=page_size, timeout=min(timeout // 1000 if timeout > 1000 else timeout, 20), retries=retries)
    dyn_result = run_coro_sync(_collect_autoinfo_policy_dynamic(start_date, end_date, timeout=timeout, max_pages=pages))
    items: List[Dict[str, Any]] = []
    if api_result.get("ok"):
        items.extend(api_result.get("items", []))
    if dyn_result.get("ok"):
        items.extend(dyn_result.get("items", []))
    items = filter_items_by_date(dedupe_items(items), start_date, end_date, strict=True)
    return {"ok": True, "source": "中国汽车工业信息网政策动态", "source_url": "https://www.autoinfo.org.cn/#/policy/dynamic/index", "start_date": start_date, "end_date": end_date, "api_count": api_result.get("count", 0), "dynamic_count": dyn_result.get("count", 0), "count": len(items), "items": items, "api_result": {k: api_result.get(k) for k in ["method", "menu_ids", "attempts"]}, "dynamic_result": {k: dyn_result.get(k) for k in ["method", "content_length", "network_item_count", "error_type", "message", "console_errors"]}}


def is_yiche_url(url: str) -> bool:
    return any(domain in (url or "") for domain in ["yiche.com", "bitauto.com", "cheyisou.com"])


def is_autohome_url(url: str) -> bool:
    return "autohome.com.cn" in (url or "")


def normalize_article_url(base_url: str, href: str) -> str:
    if not href:
        return ""
    url = urljoin(base_url, href.strip())
    url, _frag = urldefrag(url)
    return url


def date_in_range(date_str: Optional[str], start: datetime.date, end: datetime.date) -> bool:
    if not date_str:
        return False
    try:
        d = datetime.date.fromisoformat(date_str)
    except Exception:
        return False
    return start <= d <= end


def explicit_range_date(text: str, start: datetime.date, end: datetime.date) -> Optional[str]:
    cur = start
    while cur <= end:
        if cur.isoformat() in text or f"{cur.year}年{cur.month}月{cur.day}日" in text or f"{cur.month}月{cur.day}日" in text:
            return cur.isoformat()
        cur += datetime.timedelta(days=1)
    return None


def fetch_article_content(url: str, timeout: int = 15, retries: int = 1) -> Dict[str, Any]:
    last_error = ""
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            if resp.encoding is None:
                resp.encoding = resp.apparent_encoding
            html = resp.text or ""
            soup = BeautifulSoup(html, "lxml")
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            meta_date = ""
            for key in ["article:published_time", "pubdate", "publishdate", "publishDate", "date", "weibo: article:create_at"]:
                tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
                if tag and tag.get("content"):
                    meta_date = tag.get("content")
                    break
            text = trafilatura.extract(html, include_comments=False, include_tables=False) or soup.get_text("\n", strip=True)
            return {"ok": True, "url": url, "status_code": resp.status_code, "title": title, "content": clean_text(text or ""), "date": parse_date_from_text(meta_date or text or title)}
        except Exception as e:
            last_error = str(e)
            retry_sleep(attempt, 0.8)
    return {"ok": False, "url": url, "message": last_error, "error_type": classify_error(last_error)}


def build_new_car_item(title: str, summary: str, url: str, source: str, source_type: str, item_date: str, confidence: str = "high", date_confidence: str = "high") -> Dict[str, Any]:
    return {
        "title": clean_text(title).splitlines()[0][:120] if title else "",
        "summary": clean_text(summary or title)[:500],
        "content": clean_text(summary or title),
        "url": url,
        "source": source,
        "source_type": source_type,
        "date": item_date,
        "category": "产品信息",
        "confidence": confidence,
        "date_confidence": date_confidence,
    }


@mcp.tool()
def collect_autohome_newbrand(start_date: str, end_date: Optional[str] = None, timeout: int = 20, retries: int = 1, link_limit: int = 80, fetch_details: bool = True) -> dict:
    """采集汽车之家“上市新车”栏目，按标题事件日期和详情发布时间过滤新车上市/预售信息。"""
    end_date = end_date or start_date
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    source_url = "https://www.autohome.com.cn/newbrand/#pvareaid=3311231"
    result = web_fetch(source_url, timeout=timeout, extract_text=True, retries=retries, clean=True)
    if not result.get("ok"):
        return {"ok": False, "source": "汽车之家上市新车", "source_url": source_url, "start_date": start_date, "end_date": end_date, "count": 0, "items": [], "error_type": result.get("error_type"), "message": result.get("message", "")}
    seen_urls = set()
    candidates: List[Dict[str, str]] = []
    for link in result.get("links", []):
        title = clean_text(link.get("title", "")).replace("\n", " ").strip()
        url = normalize_article_url(source_url, link.get("url", ""))
        if not title or not url or url in seen_urls:
            continue
        if not re.search(r"autohome\.com\.cn/news/20\d{4}/\d+\.html", url):
            continue
        if not is_new_car_post(title):
            continue
        seen_urls.add(url)
        candidates.append({"title": title, "url": url})
        if len(candidates) >= link_limit:
            break
    items: List[Dict[str, Any]] = []
    detail_checked = 0
    for cand in candidates:
        title = cand["title"]
        url = cand["url"]
        event_date = explicit_range_date(title, start, end) or parse_date_from_text(title, default_year=start.year)
        if fetch_details:
            detail_checked += 1
            detail = fetch_article_content(url, timeout=min(timeout, 15), retries=0)
            detail_text = f"{detail.get('title', '')} {detail.get('content', '')}"
            publish_date = detail.get("date") or parse_date_from_text(detail_text, default_year=start.year)
            if date_in_range(publish_date, start, end):
                summary = detail.get("content") or title
                item = build_new_car_item(title, summary, url, "汽车之家上市新车", "autohome_newbrand", publish_date, "high", "high")
                item["publish_date"] = publish_date
                item["event_date"] = event_date
                item["date_source"] = "publish_date"
                items.append(item)
                continue
        if date_in_range(event_date, start, end):
            item = build_new_car_item(title, title, url, "汽车之家上市新车", "autohome_newbrand", event_date, "high", "medium")
            item["event_date"] = event_date
            item["date_source"] = "event_date"
            items.append(item)
            continue
    items = filter_items_by_date(dedupe_items(items), start_date, end_date, strict=True)
    return {"ok": True, "source": "汽车之家上市新车", "source_url": source_url, "start_date": start_date, "end_date": end_date, "candidate_count": len(candidates), "detail_checked": detail_checked, "count": len(items), "items": items}


@mcp.tool()
def collect_yiche_xinche_news(start_date: str, end_date: Optional[str] = None, timeout: int = 20, retries: int = 1, limit: int = 20) -> dict:
    """采集易车“新车消息”栏目；静态栏目不可用时自动退回易车站内搜索。"""
    end_date = end_date or start_date
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    source_url = "https://news.yiche.com/xinchexiaoxi/"
    items: List[Dict[str, Any]] = []
    fetch_status = web_fetch(source_url, timeout=timeout, extract_text=True, retries=retries, clean=True)
    if fetch_status.get("ok"):
        for link in fetch_status.get("links", [])[: max(limit * 2, 30)]:
            title = clean_text(link.get("title", "")).replace("\n", " ").strip()
            url = normalize_article_url(source_url, link.get("url", ""))
            if not title or not url or not is_yiche_url(url) or not is_new_car_post(title):
                continue
            item_date = explicit_range_date(title, start, end) or parse_date_from_text(title, default_year=start.year)
            if date_in_range(item_date, start, end):
                items.append(build_new_car_item(title, title, url, "易车新车消息", "yiche_xinche_news", item_date, "high", "high"))
    queries = []
    d = start
    while d <= end:
        cn_date = f"{d.year}年{d.month}月{d.day}日"
        md_date = f"{d.month}月{d.day}日"
        queries.extend([
            f"site:news.yiche.com/xinchexiaoxi 易车 新车 上市 {cn_date}",
            f"site:news.yiche.com 易车 新车 预售 {cn_date}",
            f"site:news.yiche.com 易车 {md_date} 上市 预售 亮相",
        ])
        d += datetime.timedelta(days=1)
    query_status: List[Dict[str, Any]] = []
    for query in queries:
        sr = web_search(query, limit=limit, retries=retries)
        query_status.append({"query": query, "ok": sr.get("ok"), "count": len(sr.get("results", [])), "message": sr.get("message", "")})
        if not sr.get("ok"):
            continue
        for r in sr.get("results", []):
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            url = r.get("url", "")
            text = f"{title} {snippet}"
            if not is_new_car_post(text):
                continue
            domain_ok = is_yiche_url(url) or "易车" in text or "BitAuto" in text
            if not domain_ok:
                continue
            item_date = explicit_range_date(text, start, end) or r.get("date") or parse_date_from_text(text, default_year=start.year)
            if not date_in_range(item_date, start, end):
                continue
            items.append(build_new_car_item(title[:120], snippet or title, url, "易车新车消息", "yiche_xinche_news", item_date, "high" if is_yiche_url(url) else "medium", "high" if explicit_range_date(text, start, end) else "medium"))
    items = filter_items_by_date(dedupe_items(items), start_date, end_date, strict=True)
    return {"ok": True, "source": "易车新车消息", "source_url": source_url, "start_date": start_date, "end_date": end_date, "fetch_ok": fetch_status.get("ok"), "fetch_content_length": fetch_status.get("content_length"), "query_status": query_status, "count": len(items), "items": items}


@mcp.tool()
def collect_new_car_launches(start_date: str, end_date: Optional[str] = None, timeout: int = 20, retries: int = 1, limit: int = 20) -> dict:
    """聚合采集新车上市/预售信息：易车新车消息为主，汽车之家上市新车为补充。"""
    end_date = end_date or start_date
    yiche = collect_yiche_xinche_news(start_date=start_date, end_date=end_date, timeout=timeout, retries=retries, limit=limit)
    autohome = collect_autohome_newbrand(start_date=start_date, end_date=end_date, timeout=timeout, retries=retries, link_limit=max(limit * 3, 80), fetch_details=True)
    items = []
    if yiche.get("ok"):
        items.extend(yiche.get("items", []))
    if autohome.get("ok"):
        items.extend(autohome.get("items", []))
    items = filter_items_by_date(dedupe_items(items), start_date, end_date, strict=True)
    return {"ok": True, "source": "新车上市聚合", "source_type": "new_car_launches", "start_date": start_date, "end_date": end_date, "primary_source": "易车新车消息", "supplement_source": "汽车之家上市新车", "sources": [{"name": "易车新车消息", "role": "primary", "ok": yiche.get("ok"), "count": yiche.get("count", 0), "fetch_ok": yiche.get("fetch_ok")}, {"name": "汽车之家上市新车", "role": "supplement", "ok": autohome.get("ok"), "count": autohome.get("count", 0), "candidate_count": autohome.get("candidate_count", 0)}], "count": len(items), "items": items}


@mcp.tool()
def collect_yiche_new_car_news(start_date: str, end_date: Optional[str] = None, timeout: int = 20, retries: int = 1, limit: int = 20) -> dict:
    """兼容旧工具名：改为采集“易车新车消息 + 汽车之家上市新车”的新车聚合信源。"""
    return collect_new_car_launches(start_date=start_date, end_date=end_date, timeout=timeout, retries=retries, limit=limit)


@mcp.tool()
def batch_collect_sources(sources: List[Dict[str, Any]], start_date: Optional[str] = None, end_date: Optional[str] = None, default_dynamic: bool = False, dedupe: bool = True, strict_date: bool = True, resolve_original_links: bool = False) -> dict:
    """批量采集多个信源，支持 search/fetch/dynamic，返回信源状态、日期过滤和去重结果；表格链接列仅使用原始信源链接。"""
    all_items: List[Dict[str, Any]] = []
    source_results: List[Dict[str, Any]] = []
    target_date = start_date if start_date and start_date == end_date else None
    for source in sources:
        name = source.get("name", "")
        source_type = source.get("type") or ("dynamic" if default_dynamic else "fetch")
        result: Dict[str, Any] = {}
        items: List[Dict[str, Any]] = []
        try:
            if source_type == "cls_auto_morning":
                result = collect_cls_auto_morning(start_date=start_date, end_date=end_date, timeout=int(source.get("timeout", 20)), retries=int(source.get("retries", 1)))
                if result.get("ok"):
                    items.extend(result.get("items", []))
            elif source_type == "jiemian_auto_morning":
                result = collect_jiemian_auto_morning(start_date=start_date, end_date=end_date, timeout=int(source.get("timeout", 20)), retries=int(source.get("retries", 1)), max_candidates=int(source.get("max_candidates", 30)))
                if result.get("ok"):
                    items.extend(result.get("items", []))
            elif source_type == "sina_auto_7x24":
                result = collect_sina_auto_7x24(start_date=start_date, end_date=end_date, pages=int(source.get("pages", 12)), limit=int(source.get("limit", 20)), timeout=int(source.get("timeout", 20)), retries=int(source.get("retries", 1)))
                if result.get("ok"):
                    items.extend(result.get("items", []))
            elif source_type == "autoinfo_policy_dynamic":
                result = collect_autoinfo_policy_dynamic(start_date=start_date, end_date=end_date, pages=int(source.get("pages", 8)), page_size=int(source.get("page_size", 10)), timeout=int(source.get("timeout", 60000)), retries=int(source.get("retries", 1)))
                if result.get("ok"):
                    items.extend(result.get("items", []))
            elif source_type == "yiche_weibo_new_cars":
                result = collect_yiche_weibo_new_cars(start_date=start_date, end_date=end_date, weibo_cookie=source.get("cookie_string"), timeout=int(source.get("timeout", 60000)), scroll_times=int(source.get("scroll_times", 8)), retries=int(source.get("retries", 1)))
                if result.get("ok"):
                    items.extend(result.get("items", []))
            elif source_type == "autohome_newbrand":
                result = collect_autohome_newbrand(start_date=start_date, end_date=end_date, timeout=int(source.get("timeout", 20)), retries=int(source.get("retries", 1)), link_limit=int(source.get("link_limit", 80)), fetch_details=bool(source.get("fetch_details", True)))
                if result.get("ok"):
                    items.extend(result.get("items", []))
            elif source_type == "yiche_xinche_news":
                result = collect_yiche_xinche_news(start_date=start_date, end_date=end_date, timeout=int(source.get("timeout", 20)), retries=int(source.get("retries", 1)), limit=int(source.get("limit", 20)))
                if result.get("ok"):
                    items.extend(result.get("items", []))
            elif source_type in {"new_car_launches", "yiche_new_car_news"}:
                result = collect_new_car_launches(start_date=start_date, end_date=end_date, timeout=int(source.get("timeout", 20)), retries=int(source.get("retries", 1)), limit=int(source.get("limit", 20)))
                if result.get("ok"):
                    items.extend(result.get("items", []))
            elif source_type == "search":
                result = web_search(keyword=source.get("keyword") or source.get("query"), limit=int(source.get("limit", 10)), retries=int(source.get("retries", 1)))
                if result.get("ok"):
                    for r in result.get("results", []):
                        items.append(enrich_item({"title": r.get("title", ""), "summary": r.get("snippet", ""), "url": r.get("url", ""), "date": r.get("date")}, name, "search", target_date))
            elif source_type == "fetch":
                result = web_fetch(url=source.get("url"), timeout=int(source.get("timeout", 15)), extract_text=bool(source.get("extract_text", True)), retries=int(source.get("retries", 2)), clean=bool(source.get("clean", True)))
                if result.get("ok"):
                    links = result.get("links", [])
                    for link in links[: int(source.get("link_limit", 20))]:
                        items.append(enrich_item({"title": link.get("title", ""), "summary": link.get("title", ""), "url": link.get("url", "")}, name, "fetch_link", target_date))
                    if not items:
                        items.append(enrich_item({"title": result.get("title", ""), "summary": result.get("content", "")[:500], "content": result.get("content", ""), "url": result.get("url", ""), "date": result.get("date")}, name, "fetch", target_date))
            elif source_type == "dynamic":
                result = web_fetch_dynamic(url=source.get("url"), timeout=int(source.get("timeout", 30000)), wait_until=source.get("wait_until", "networkidle"), wait_selector=source.get("wait_selector"), scroll=bool(source.get("scroll", True)), scroll_times=int(source.get("scroll_times", 3)), extract_text=bool(source.get("extract_text", True)), headless=bool(source.get("headless", True)), cookie_string=source.get("cookie_string"), cookie_domain=source.get("cookie_domain"), retries=int(source.get("retries", 2)), clean=bool(source.get("clean", True)))
                if result.get("ok"):
                    content = result.get("content", "")
                    items = extract_items_by_lines(content, result.get("url", ""), name)
                    if not items:
                        items.append(enrich_item({"title": result.get("title", ""), "summary": content[:500], "content": content, "url": result.get("url", ""), "date": result.get("date")}, name, "dynamic", target_date))
            else:
                result = {"ok": False, "message": f"Unsupported source type: {source_type}", "error_type": "unsupported_source_type"}
            items = [enrich_item(i, name, source_type, target_date) for i in items]
            items = filter_items_by_date(items, start_date, end_date, strict=strict_date)
            source_results.append(build_source_status(name, source_type, result, len(items)))
            all_items.extend(items)
        except Exception as e:
            source_results.append({"name": name, "ok": False, "fetch_ok": False, "type": source_type, "count": 0, "error_type": classify_error(str(e)), "message": f"Error: {str(e)}"})
    if dedupe:
        all_items = dedupe_items(all_items)
    table_rows = items_to_table_rows(all_items, default_date=start_date if start_date == end_date else None, resolve_links=resolve_original_links)
    missing_original_link_count = sum(1 for r in table_rows if not r.get("链接"))
    success_sources = sum(1 for s in source_results if s.get("ok"))
    fetch_success = sum(1 for s in source_results if s.get("fetch_ok"))
    verified = sum(1 for i in all_items if i.get("confidence") in {"high", "medium"})
    coverage_score = round((success_sources / max(len(source_results), 1)) * 0.5 + min(len(all_items), 30) / 30 * 0.5, 3)
    return {
        "ok": True,
        "start_date": start_date,
        "end_date": end_date,
        "sources": source_results,
        "count": len(all_items),
        "items": all_items,
        "table_columns": TABLE_COLUMNS,
        "table_rows": table_rows,
        "table_workbook": build_table_workbook_payload(table_rows, sheet_name=f"{start_date or ''}行业快讯"[:31] or "行业快讯"),
        "link_quality": {
            "resolve_original_links": resolve_original_links,
            "missing_original_link_count": missing_original_link_count,
            "original_link_count": len(table_rows) - missing_original_link_count,
            "rule": "链接列仅允许原始链接：微信公众号 > 官方微博 > 官网/垂媒 > 新浪财经；早报/汇总/拼盘信源不写入链接列。",
        },
        "quality": {
            "coverage_score": coverage_score,
            "source_success_rate": round(success_sources / max(len(source_results), 1), 3),
            "fetch_success_rate": round(fetch_success / max(len(source_results), 1), 3),
            "verified_item_count": verified,
            "low_confidence_item_count": sum(1 for i in all_items if i.get("confidence") == "low"),
            "is_sufficient_for_daily_report": len(all_items) >= 15 and success_sources >= 3,
            "blocking_issues": [s for s in source_results if not s.get("ok")],
        },
    }


@mcp.tool()
def collect_auto_5_sources(date: str, weibo_cookie: Optional[str] = None, resolve_original_links: bool = False) -> dict:
    """按指定日期快捷采集汽车行业五大信源。date 格式：YYYY-MM-DD。resolve_original_links=True 时会按优先级搜索补原始链接。"""
    d = datetime.date.fromisoformat(date)
    cn_date = f"{d.year}年{d.month}月{d.day}日"
    sources = [
        {"name": "财联社汽车早报", "type": "cls_auto_morning", "timeout": 20, "retries": 1},
        {"name": "界面新闻汽车早报", "type": "jiemian_auto_morning", "timeout": 20, "retries": 1, "max_candidates": 30},
        {"name": "中国汽车工业信息网政策动态", "type": "autoinfo_policy_dynamic", "pages": 8, "page_size": 10, "timeout": 60000, "retries": 1},
        {"name": "新浪汽车7x24快讯", "type": "sina_auto_7x24", "pages": 12, "limit": 20, "timeout": 20, "retries": 1},
        {"name": "新浪汽车行业", "type": "fetch", "url": "https://auto.sina.com.cn/news/", "timeout": 20, "retries": 1, "link_limit": 30},
        {"name": "新车上市聚合", "type": "new_car_launches", "timeout": 20, "retries": 1, "limit": 20},
    ]
    result = batch_collect_sources(sources=sources, start_date=date, end_date=date, default_dynamic=False, dedupe=True, strict_date=True, resolve_original_links=resolve_original_links)
    result["date"] = date
    result["mode"] = "auto_daily_5_sources"
    result["table_sheet_name"] = f"{d.month}.{d.day}行业快讯"
    result["table_workbook"] = build_table_workbook_payload(result.get("table_rows", []), sheet_name=result["table_sheet_name"])
    result["warnings"] = []
    if not result.get("quality", {}).get("is_sufficient_for_daily_report"):
        result["warnings"].append("当前采集质量未达到完整日报阈值，建议检查搜索 API、动态抓取或站点 Adapter。")
    return result


if __name__ == "__main__":
    print(f"Starting MCP server: name=information_fetch host={HOST} port={PORT} transport={MCP_TRANSPORT}")
    mcp.run(transport=MCP_TRANSPORT)
