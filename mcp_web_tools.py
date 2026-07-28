import os
import re
import time
import asyncio
import datetime
from typing import Optional, List, Dict, Any

import requests
import trafilatura
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from mcp.server.fastmcp import FastMCP

PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "sse")

mcp = FastMCP("information_fetch", host=HOST, port=PORT)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def retry_sleep(attempt: int, base_sleep: float = 1.5):
    time.sleep(base_sleep * (attempt + 1))


def clean_text(text: str) -> str:
    if not text:
        return ""

    noise_patterns = [
        r"分享到.*", r"分享至.*", r"打开微信.*", r"扫描二维码.*",
        r"责任编辑[:：].*", r"免责声明[:：].*", r"版权声明[:：].*", r"版权所有.*",
        r"登录后.*", r"请先登录.*", r"点击查看更多.*", r"展开全文.*", r"收起全文.*",
        r"返回搜狐.*", r"广告.*",
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

    cleaned = []
    last = None
    for line in lines:
        if line != last:
            cleaned.append(line)
        last = line
    return "\n".join(cleaned).strip()


def parse_date_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    now_year = datetime.datetime.now().year
    patterns = [
        r"(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日",
        r"(?P<y>\d{4})[-/\.](?P<m>\d{1,2})[-/\.](?P<d>\d{1,2})",
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


def parse_cookie_string(cookie_string: Optional[str], domain: str) -> List[Dict[str, Any]]:
    if not cookie_string:
        return []
    cookies = []
    for pair in cookie_string.split(";"):
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            cookies.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return cookies


def filter_items_by_date(items: List[Dict[str, Any]], start_date: Optional[str], end_date: Optional[str]) -> List[Dict[str, Any]]:
    if not start_date and not end_date:
        return items
    filtered = []
    for item in items:
        item_date = item.get("date")
        if not item_date:
            text = " ".join(str(item.get(k, "")) for k in ["title", "summary", "content"])
            item_date = parse_date_from_text(text)
            if item_date:
                item["date"] = item_date
        if not item_date:
            continue
        try:
            d = datetime.date.fromisoformat(item_date)
            if start_date and d < datetime.date.fromisoformat(start_date):
                continue
            if end_date and d > datetime.date.fromisoformat(end_date):
                continue
            filtered.append(item)
        except Exception:
            continue
    return filtered


def extract_items_by_lines(content: str) -> List[Dict[str, Any]]:
    if not content:
        return []
    items = []
    for line in content.splitlines():
        line = line.strip()
        if len(line) < 8:
            continue
        if line in {"首页", "新闻", "汽车", "财经", "科技", "更多", "登录", "注册"}:
            continue
        items.append({"title": line[:80], "date": parse_date_from_text(line), "summary": line, "url": ""})
    return items


def dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        title = item.get("title", "").strip()
        url = item.get("url", "").strip()
        summary = item.get("summary", "").strip()
        key = url or title or summary[:50]
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


@mcp.tool()
def web_search(keyword: str, limit: int = 10, retries: int = 2) -> dict:
    """根据关键词搜索互联网内容，返回标题、摘要、URL。需要 Render 环境变量 BING_SEARCH_API_KEY。"""
    api_key = os.getenv("BING_SEARCH_API_KEY")
    if not api_key:
        return {"ok": False, "keyword": keyword, "message": "Error: missing BING_SEARCH_API_KEY"}

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
            results = []
            for item in webpages[:limit]:
                results.append({
                    "title": item.get("name", ""),
                    "snippet": item.get("snippet", ""),
                    "url": item.get("url", ""),
                    "date": parse_date_from_text(item.get("name", "") + " " + item.get("snippet", "")),
                })
            return {"ok": True, "keyword": keyword, "results": results}
        except Exception as e:
            last_error = str(e)
            if attempt < retries:
                retry_sleep(attempt)
    return {"ok": False, "keyword": keyword, "message": f"Error: {last_error}"}


@mcp.tool()
def web_fetch(url: str, timeout: int = 15, extract_text: bool = True, retries: int = 2, clean: bool = True) -> dict:
    """抓取静态网页内容并提取正文。"""
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
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
            return {"ok": True, "url": resp.url, "title": title, "content": content, "date": parse_date_from_text(title + "\n" + content), "status_code": resp.status_code}
        except Exception as e:
            last_error = str(e)
            if attempt < retries:
                retry_sleep(attempt)
    return {"ok": False, "url": url, "message": f"Error: {last_error}"}


async def _dynamic_once(url: str, timeout: int, wait_until: str, wait_selector: Optional[str], scroll: bool, scroll_times: int, extract_text: bool, headless: bool, cookie_string: Optional[str], cookie_domain: Optional[str], clean: bool) -> dict:
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(user_agent=DEFAULT_HEADERS["User-Agent"], locale="zh-CN", viewport={"width": 1440, "height": 1200})
            if cookie_string and cookie_domain:
                cookies = parse_cookie_string(cookie_string, cookie_domain)
                if cookies:
                    await context.add_cookies(cookies)
            page = await context.new_page()
            response = await page.goto(url, wait_until=wait_until, timeout=timeout)
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=timeout)
            await page.wait_for_timeout(2000)
            if scroll:
                for _ in range(scroll_times):
                    await page.mouse.wheel(0, 1200)
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
            await context.close()
            await browser.close()
            return {"ok": True, "url": final_url, "title": title, "content": content, "date": parse_date_from_text(title + "\n" + content), "status_code": status_code}
    except Exception as e:
        if browser:
            await browser.close()
        return {"ok": False, "url": url, "message": f"Error: {str(e)}"}


@mcp.tool()
def web_fetch_dynamic(url: str, timeout: int = 30000, wait_until: str = "networkidle", wait_selector: Optional[str] = None, scroll: bool = True, scroll_times: int = 3, extract_text: bool = True, headless: bool = True, cookie_string: Optional[str] = None, cookie_domain: Optional[str] = None, retries: int = 2, clean: bool = True) -> dict:
    """使用浏览器渲染抓取动态网页内容，适合微博、SPA、前端渲染页面、需要滚动加载页面。"""
    last_result = None
    for attempt in range(retries + 1):
        result = asyncio.run(_dynamic_once(url, timeout, wait_until, wait_selector, scroll, scroll_times, extract_text, headless, cookie_string, cookie_domain, clean))
        last_result = result
        if result.get("ok"):
            return result
        if attempt < retries:
            retry_sleep(attempt)
    return last_result or {"ok": False, "url": url, "message": "Error: unknown dynamic fetch error"}


@mcp.tool()
def batch_collect_sources(sources: List[Dict[str, Any]], start_date: Optional[str] = None, end_date: Optional[str] = None, default_dynamic: bool = False, dedupe: bool = True) -> dict:
    """批量采集多个信源，支持 search/fetch/dynamic，支持日期过滤和去重。"""
    all_items = []
    source_results = []
    for source in sources:
        name = source.get("name", "")
        source_type = source.get("type") or ("dynamic" if default_dynamic else "fetch")
        try:
            if source_type == "search":
                result = web_search(keyword=source.get("keyword") or source.get("query"), limit=int(source.get("limit", 10)))
                items = []
                if result.get("ok"):
                    for r in result.get("results", []):
                        items.append({"source": name, "source_type": "search", "title": r.get("title", ""), "summary": r.get("snippet", ""), "url": r.get("url", ""), "date": r.get("date")})
                items = filter_items_by_date(items, start_date, end_date)
                source_results.append({"name": name, "ok": result.get("ok"), "type": source_type, "count": len(items), "message": result.get("message", "")})
                all_items.extend(items)
            elif source_type == "fetch":
                result = web_fetch(url=source.get("url"), timeout=int(source.get("timeout", 15)), extract_text=bool(source.get("extract_text", True)), retries=int(source.get("retries", 2)), clean=bool(source.get("clean", True)))
                items = []
                if result.get("ok"):
                    items.append({"source": name, "source_type": "fetch", "title": result.get("title", ""), "summary": result.get("content", "")[:500], "content": result.get("content", ""), "url": result.get("url", ""), "date": result.get("date")})
                items = filter_items_by_date(items, start_date, end_date)
                source_results.append({"name": name, "ok": result.get("ok"), "type": source_type, "count": len(items), "message": result.get("message", "")})
                all_items.extend(items)
            elif source_type == "dynamic":
                result = web_fetch_dynamic(url=source.get("url"), timeout=int(source.get("timeout", 30000)), wait_until=source.get("wait_until", "networkidle"), wait_selector=source.get("wait_selector"), scroll=bool(source.get("scroll", True)), scroll_times=int(source.get("scroll_times", 3)), extract_text=bool(source.get("extract_text", True)), headless=bool(source.get("headless", True)), cookie_string=source.get("cookie_string"), cookie_domain=source.get("cookie_domain"), retries=int(source.get("retries", 2)), clean=bool(source.get("clean", True)))
                items = []
                if result.get("ok"):
                    content = result.get("content", "")
                    line_items = extract_items_by_lines(content)
                    for item in line_items:
                        item.update({"source": name, "source_type": "dynamic", "url": result.get("url", "")})
                    items.extend(line_items)
                    if not items:
                        items.append({"source": name, "source_type": "dynamic", "title": result.get("title", ""), "summary": content[:500], "content": content, "url": result.get("url", ""), "date": result.get("date")})
                items = filter_items_by_date(items, start_date, end_date)
                source_results.append({"name": name, "ok": result.get("ok"), "type": source_type, "count": len(items), "message": result.get("message", "")})
                all_items.extend(items)
            else:
                source_results.append({"name": name, "ok": False, "type": source_type, "count": 0, "message": f"Unsupported source type: {source_type}"})
        except Exception as e:
            source_results.append({"name": name, "ok": False, "type": source_type, "count": 0, "message": f"Error: {str(e)}"})
    if dedupe:
        all_items = dedupe_items(all_items)
    return {"ok": True, "start_date": start_date, "end_date": end_date, "sources": source_results, "count": len(all_items), "items": all_items}


@mcp.tool()
def collect_auto_5_sources(date: str, weibo_cookie: Optional[str] = None) -> dict:
    """按指定日期快捷采集汽车行业五大信源。date 格式：YYYY-MM-DD。"""
    d = datetime.date.fromisoformat(date)
    cn_date = f"{d.year}年{d.month}月{d.day}日"
    sources = [
        {"name": "财联社汽车早报", "type": "search", "keyword": f"财联社 汽车早报 {cn_date}", "limit": 10},
        {"name": "界面新闻汽车早报", "type": "search", "keyword": f"界面新闻 汽车早报 {cn_date}", "limit": 10},
        {"name": "中国汽车工业信息网", "type": "dynamic", "url": "https://www.autoinfo.org.cn/#/policy/dynamic/index", "scroll": True, "scroll_times": 5, "wait_until": "networkidle"},
        {"name": "新浪汽车7x24快讯", "type": "dynamic", "url": "https://auto.sina.com.cn/7x24/?tagid=1", "scroll": True, "scroll_times": 5, "wait_until": "networkidle"},
        {"name": "易车微博", "type": "dynamic", "url": "https://weibo.com/u/1912222221", "scroll": True, "scroll_times": 8, "wait_until": "networkidle", "cookie_string": weibo_cookie, "cookie_domain": ".weibo.com" if weibo_cookie else None},
    ]
    return batch_collect_sources(sources=sources, start_date=date, end_date=date, default_dynamic=False, dedupe=True)


if __name__ == "__main__":
    print(f"Starting MCP server: name=information_fetch host={HOST} port={PORT} transport={MCP_TRANSPORT}")
    mcp.run(transport=MCP_TRANSPORT)
