import os
import re
import time
import asyncio
import datetime
import threading
from difflib import SequenceMatcher
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import quote_plus

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


@mcp.tool()
def batch_collect_sources(sources: List[Dict[str, Any]], start_date: Optional[str] = None, end_date: Optional[str] = None, default_dynamic: bool = False, dedupe: bool = True, strict_date: bool = True) -> dict:
    """批量采集多个信源，支持 search/fetch/dynamic，返回信源状态、日期过滤和去重结果。"""
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
def collect_auto_5_sources(date: str, weibo_cookie: Optional[str] = None) -> dict:
    """按指定日期快捷采集汽车行业五大信源。date 格式：YYYY-MM-DD。"""
    d = datetime.date.fromisoformat(date)
    cn_date = f"{d.year}年{d.month}月{d.day}日"
    md_date = f"{d.month}月{d.day}日"
    sources = [
        {"name": "财联社汽车早报", "type": "cls_auto_morning", "timeout": 20, "retries": 1},
        {"name": "界面新闻汽车早报", "type": "search", "keyword": f"界面新闻 汽车早报 {cn_date}", "limit": 10, "retries": 1},
        {"name": "界面新闻汽车早报-站内", "type": "search", "keyword": f"site:jiemian.com 汽车早报 {md_date}", "limit": 10, "retries": 1},
        {"name": "中国汽车工业信息网", "type": "dynamic", "url": "https://www.autoinfo.org.cn/#/policy/dynamic/index", "scroll": True, "scroll_times": 5, "wait_until": "networkidle", "timeout": 60000, "retries": 1},
        {"name": "新浪汽车7x24快讯", "type": "dynamic", "url": "https://auto.sina.com.cn/7x24/?tagid=1", "scroll": True, "scroll_times": 5, "wait_until": "networkidle", "timeout": 60000, "retries": 1},
        {"name": "新浪汽车行业", "type": "fetch", "url": "https://auto.sina.com.cn/news/", "timeout": 20, "retries": 1, "link_limit": 30},
        {"name": "易车微博", "type": "dynamic", "url": "https://weibo.com/u/1912222221", "scroll": True, "scroll_times": 8, "wait_until": "networkidle", "timeout": 60000, "cookie_string": weibo_cookie, "cookie_domain": ".weibo.com" if weibo_cookie else None, "retries": 1},
        {"name": "易车新闻兜底", "type": "search", "keyword": f"易车 新车 上市 {cn_date}", "limit": 10, "retries": 1},
    ]
    result = batch_collect_sources(sources=sources, start_date=date, end_date=date, default_dynamic=False, dedupe=True, strict_date=True)
    result["date"] = date
    result["mode"] = "auto_daily_5_sources"
    result["warnings"] = []
    if not weibo_cookie:
        result["warnings"].append("易车微博未提供 Cookie；如页面进入访客系统，将自动标记 login_required，并使用易车新闻搜索兜底。")
    if not result.get("quality", {}).get("is_sufficient_for_daily_report"):
        result["warnings"].append("当前采集质量未达到完整日报阈值，建议检查搜索 API、动态抓取、微博 Cookie 或站点 Adapter。")
    return result


if __name__ == "__main__":
    print(f"Starting MCP server: name=information_fetch host={HOST} port={PORT} transport={MCP_TRANSPORT}")
    mcp.run(transport=MCP_TRANSPORT)
