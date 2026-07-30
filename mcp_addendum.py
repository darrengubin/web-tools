"""
MCP 增强模块 — 6大信源专用采集器
逐个调用 mcp_web_tools.py 中每个信源的专用采集方法。
"""

import datetime, time
from typing import Optional

# ---- 易车 Cookie 存储（通过 set_yiche_cookie 设置） ----
_yiche_cookie_global = None

# ============================================================
# 政府机构 → 官网域名映射（autoinfo 链接补充用）
# ============================================================
GOV_DOMAINS = {
    "工业和信息化部":"miit.gov.cn","工信部":"miit.gov.cn",
    "装备工业一司":"miit.gov.cn","节能与综合利用司":"miit.gov.cn",
    "国家发展改革委":"ndrc.gov.cn","发改委":"ndrc.gov.cn","发展改革委":"ndrc.gov.cn",
    "国家能源局":"nea.gov.cn","商务部":"mofcom.gov.cn",
    "交通运输部":"mot.gov.cn","国家市场监管总局":"samr.gov.cn",
    "财政部":"mof.gov.cn","科技部":"most.gov.cn","生态环境部":"mee.gov.cn",
    "公安部":"mps.gov.cn","国家统计局":"stats.gov.cn",
    "国务院":"gov.cn","北京":"beijing.gov.cn","上海":"shanghai.gov.cn",
    "天津":"tj.gov.cn","重庆":"cq.gov.cn","广东":"gd.gov.cn",
    "深圳":"sz.gov.cn","广州":"gz.gov.cn","清远":"qingyuan.gov.cn",
    "湛江":"zhanjiang.gov.cn","浙江":"zj.gov.cn","杭州":"hangzhou.gov.cn",
    "江苏":"jiangsu.gov.cn","安徽":"ah.gov.cn","合肥":"hefei.gov.cn",
    "四川":"sc.gov.cn","成都":"chengdu.gov.cn","湖北":"hubei.gov.cn",
    "随州":"suizhou.gov.cn","云南":"yn.gov.cn","西双版纳":"xsbn.gov.cn",
    "福建":"fj.gov.cn","山东":"shandong.gov.cn","济南":"jinan.gov.cn",
    "湖南":"hunan.gov.cn","江西":"jiangxi.gov.cn","南昌":"nc.gov.cn",
    "贵州":"guizhou.gov.cn","广西":"gxzf.gov.cn","陕西":"shaanxi.gov.cn",
    "甘肃":"gansu.gov.cn","青海":"qinghai.gov.cn","宁夏":"nx.gov.cn",
    "新疆":"xinjiang.gov.cn","海南":"hainan.gov.cn","西藏":"xizang.gov.cn",
    "河南":"henan.gov.cn","河北":"hebei.gov.cn","山西":"shanxi.gov.cn",
    "辽宁":"ln.gov.cn","吉林":"jl.gov.cn","黑龙江":"hlj.gov.cn",
}


def _gov_link(source: str) -> tuple:
    """匹配政府域名，返回 (url, link_source)"""
    for name, domain in GOV_DOMAINS.items():
        if name in source:
            return f"https://www.{domain}", f"官网({domain})"
    return "", ""



# ============================================================
# autoinfo 内联 API 采集器（替换 collect_autoinfo_policy_by_api）
# ============================================================
def _collect_autoinfo_api(date: str, end: str) -> list:
    """直接调 autoinfo 已知的4个API采集"""
    try:
        import requests as req
        base = "https://www.autoinfo.org.cn/prod-api"
        hdrs = {
            "User-Agent": "Mozilla/5.0 Chrome/126.0.0.0",
            "Accept": "application/json",
            "Referer": "https://www.autoinfo.org.cn/",
        }
        items = []
        for api, p, label in [
            ("/api/policy/ttPolicy/newPolicy", {"flag": "0"}, "最新政策"),
            ("/api/policy/ttPolicyReport/policyReport", {}, "政策报道"),
            ("/api/policy/ttPolicyInterpret/localOriginal", {"unscrambleUnit": "1"}, "最新原创"),
            ("/api/policy/ttPolicyInterpret/policyExplain", {}, "政策解读"),
        ]:
            q = {**p, "pageNum": 1, "pageSize": 100}
            r = req.get(base + api, params=q, headers=hdrs, timeout=15)
            if r.status_code != 200:
                continue
            d = r.json()
            if d.get("code") != 200:
                continue
            for row in d.get("data", []):
                pub = str(row.get("publishDate") or row.get("publicDate") or "")[:10]
                if date <= pub <= end:
                    src = row.get("source") or row.get("policyResource") or label
                    title = (row.get("title") or "")[:200]
                    summary = row.get("summary") or row.get("introduction") or row.get("mainPoint") or ""
                    gov_url, gov_src = _gov_link(src)
                    items.append({
                        "title": title, "source": src, "type": label,
                        "publish_date": pub,
                        "summary": (summary or "")[:500],
                        "url": gov_url,
                        "link_source": gov_src,
                        "source_type": "autoinfo",
                        "category": infer_category(title, summary),
                    })
        return items
    except Exception:
        return []


# ============================================================
# 界面新闻 内联采集器（替换 collect_jiemian_auto_morning）
# ============================================================
def _collect_jiemian_inline(date: str, end: str) -> list:
    """从界面新闻列表页提取时间信息，解决日期匹配问题"""
    try:
        import requests as req
        from bs4 import BeautifulSoup
        import re, datetime

        hdrs = {"User-Agent": "Mozilla/5.0 Chrome/126.0.0.0"}
        today = datetime.date.today()
        items = []
        seen_urls = {}

        for page in range(1, 4):
            resp = req.get(f"https://m.jiemian.com/lists/51_{page}.html", headers=hdrs, timeout=15)
            soup = BeautifulSoup(resp.text, "lxml")

            # 第一遍：收集所有汽车早报的URL和标题
            articles = {}
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not re.search(r"jiemian\.com/article/\d+\.html", href):
                    continue
                title = a.get_text(strip=True)
                if "汽车早报" not in title:
                    continue
                url = "https:" + href if href.startswith("//") else \
                      "https://m.jiemian.com" + href if href.startswith("/") else href
                if url not in articles:
                    articles[url] = title

            # 第二遍：从span中找时间文本
            # 界面列表结构：<div class="news-footer"><span>昨天08:33</span></div>
            time_map = {}
            for span in soup.find_all("span"):
                text = span.get_text(strip=True)
                if not ("今天" in text or "昨天" in text or "前天" in text or re.match(r"\d{4}[-/]\d", text)):
                    continue
                parent = span.find_parent(["div", "p"])
                if not parent:
                    continue
                prev_a = parent.find_previous("a", href=True)
                if not prev_a:
                    continue
                href = prev_a["href"]
                if not re.search(r"jiemian\.com/article/\d+\.html", href):
                    continue
                url = "https:" + href if href.startswith("//") else \
                      "https://m.jiemian.com" + href if href.startswith("/") else href
                if url in articles and url not in time_map:
                    time_map[url] = text

            for url, title in articles.items():
                time_text = time_map.get(url, "")
                pub_date = None
                m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", time_text)
                if m:
                    pub_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                elif "今天" in time_text:
                    pub_date = today.isoformat()
                elif "昨天" in time_text:
                    pub_date = (today - datetime.timedelta(days=1)).isoformat()
                elif "前天" in time_text:
                    pub_date = (today - datetime.timedelta(days=2)).isoformat()

                if pub_date and date <= pub_date <= end and url not in seen_urls:
                    seen_urls[url] = pub_date
                    try:
                        detail = req.get(url, headers=hdrs, timeout=10)
                        detail_text = BeautifulSoup(detail.text, "lxml").get_text()
                    except Exception:
                        detail_text = ""
                    sub_items = _split_jiemian_detail(detail_text, title, url, pub_date)
                    if not sub_items:
                        sub_items.append({"title": title, "summary": title, "url": url,
                            "source": "界面新闻汽车早报", "source_type": "jiemian_auto_morning",
                            "publish_date": pub_date, "type": "行业新闻"})
                    items.extend(sub_items)
        return items
    except Exception:
        return []


def _split_jiemian_detail(text: str, main_title: str, url: str, pub_date: str) -> list:
    """从界面早报文本中拆分独立新闻条目"""
    import re
    items = []
    lines = [l.strip() for l in text.splitlines() if l.strip() and len(l.strip()) > 10]
    current_title = None
    current_body = []

    for line in lines:
        if any(s in line for s in ["界面新闻", "未经正式授权", "版权", "广告", "责任编辑", "关于我们"]):
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
            if current_title:
                summary = " ".join(current_body).strip()
                items.append({"title": current_title, "summary": summary or current_title})
            current_title = line
            current_body = []
        elif current_title:
            current_body.append(line)

    if current_title:
        summary = " ".join(current_body).strip()
        items.append({"title": current_title, "summary": summary or current_title})

    # 格式化输出
    result = []
    for it in items:
        if len(it.get("title", "")) >= 6:
            result.append({
                "title": it["title"], "summary": it.get("summary", ""),
                "url": url, "source": "界面新闻汽车早报",
                "source_type": "jiemian_auto_morning",
                "publish_date": pub_date, "type": "行业新闻",
            })
    return result


# ============================================================
# 易车 xinchexiaoxi 内联采集器（无验证码！）
# ============================================================
def _collect_yiche_xinchexiaoxi(date: str, end: str, cookies: str = None) -> list:
    """采集 news.yiche.com/xinchexiaoxi/（支持Cookie绕过验证码）"""
    try:
        import requests as req
        from bs4 import BeautifulSoup
        import re, datetime

        hdrs = {"User-Agent": "Mozilla/5.0 Chrome/126.0.0.0"}
        if cookies:
            # 将JSON cookie转为cookie dict
            try:
                import json as j
                c_list = j.loads(cookies)
                hdrs["Cookie"] = "; ".join(f"{c['name']}={c['value']}" for c in c_list if isinstance(c, dict) and 'name' in c and 'value' in c)
            except Exception:
                # 也支持直接cookie字符串
                if '=' in cookies:
                    hdrs["Cookie"] = cookies
        today = datetime.date.today()
        items = []
        seen = set()

        resp = req.get("https://news.yiche.com/xinchexiaoxi/", headers=hdrs, timeout=15)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "lxml")

        for wrapper in soup.find_all("div", class_=re.compile(r"news-main|other-news-list")):
            for a in wrapper.find_all("a", href=True):
                href = a["href"]
                title = a.get_text(strip=True)
                if not title or len(title) < 10:
                    continue
                if not re.search(r"/xinchexiaoxi/\d+", href):
                    continue

                url = href if href.startswith("http") else \
                      "https://news.yiche.com" + href if href.startswith("/") else href
                if url in seen:
                    continue
                seen.add(url)

                # 从URL提取日期: /20260729/ → 2026-07-29
                m = re.search(r"/xinchexiaoxi/(\d{4})(\d{2})(\d{2})/", url)
                pub_date = None
                if m:
                    try:
                        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                        pub_date = d.isoformat()
                    except Exception:
                        pass

                if pub_date and date <= pub_date <= end:
                    items.append({
                        "title": title[:120], "summary": title,
                        "url": url, "source": "易车新车消息",
                        "source_type": "yiche_news_direct",
                        "publish_date": pub_date, "type": "产品信息",
                    })
        return items
    except Exception:
        return []


# ============================================================
# 易车 Playwright 采集器（备用，需验证码登录）
# ============================================================

async def _yiche_pw(start, end, timeout=30000, scroll=3, cookies=None):
    items = []
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}, locale="zh-CN")
            # 如果有登录态 Cookie，注入到浏览器上下文
            if cookies:
                try:
                    if isinstance(cookies, str):
                        cookies = json.loads(cookies)
                    if isinstance(cookies, list):
                        await ctx.add_cookies(cookies)
                except Exception:
                    pass
            page = await ctx.new_page()
            await page.goto("https://news.yiche.com/xinche/", wait_until="networkidle", timeout=timeout)
            await page.wait_for_timeout(3000)
            for _ in range(scroll):
                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(1500)
            links = await page.eval_on_selector_all(
                "a[href*='news.yiche.com']",
                "els => els.map(el => ({href: el.href, text: el.textContent.trim()}))")
            seen = set()
            for link in links:
                t = (link.get("text") or "").strip()
                u = link.get("href") or ""
                if not t or not u or u in seen or len(t) < 8:
                    continue
                seen.add(u)
                for d in range((end - start).days + 1):
                    day = start + datetime.timedelta(days=d)
                    ds = f"{day.month}月{day.day}日"
                    if ds in t:
                        items.append({
                            "title": t[:120], "summary": t, "url": u,
                            "source": "易车新车", "source_type": "yiche_news_direct",
                            "publish_date": day.isoformat(), "type": "产品信息",
                        })
                        break
            await ctx.close()
            await browser.close()
    except Exception:
        pass
    return items


# ============================================================
# 主工具：6大信源统一采集
# ============================================================

@mcp.tool()
def collect_all_sources(
    date: str = None,
    end_date: str = None,
    mode: str = "auto",
    include_yiche: bool = True,
    yiche_cookie: str = None,
    include_autoinfo: bool = True,
    include_cls: bool = True,
    include_jiemian: bool = True,
    include_sina: bool = True,
    include_autohome: bool = True,
) -> dict:
    """6大信源统一采集——按信源顺序逐一执行

    自动计算时间窗口（mode=auto）：
    - 平时：前一天12:00 → 今天12:00
    - 周一：上周五12:00 → 周一12:00

    每个信源是独立的MCP工具，也可单独调用。
    """
    # ---- 时间窗口计算 ----
    now = datetime.datetime.now()
    today = now.date()
    if mode == "auto":
        if now.hour < 12:
            start_date = today - datetime.timedelta(days=1)
            end_date_calc = today
            if today.weekday() == 0:
                start_date = today - datetime.timedelta(days=3)
        else:
            start_date = today
            end_date_calc = today + datetime.timedelta(days=1)
        start_str = start_date.isoformat()
        end_str = end_date_calc.isoformat()
    else:
        start_str = date
        end_str = end_date or date

    date_param = start_str
    end_param = end_str

    # ---- 历史去重 ----
    dedup_file = "/tmp/.collected_urls.json"
    seen_urls = set()
    if os.path.exists(dedup_file):
        try:
            with open(dedup_file, "r", encoding="utf-8") as f:
                seen_urls = set(json.load(f))
        except Exception:
            seen_urls = set()

    all_items = []
    source_results = []

    # [1] 中国汽车工业信息网
    if include_autoinfo:
        r = collect_autoinfo(date=date_param, end_date=end_param)
        if r.get("items"):
            all_items.extend(r["items"])
        source_results.append(r)

    # [2] 财联社
    if include_cls:
        r = collect_cls(date=date_param, end_date=end_param)
        if r.get("items"):
            all_items.extend(r["items"])
        source_results.append(r)

    # [3] 界面新闻
    if include_jiemian:
        r = collect_jiemian(date=date_param, end_date=end_param)
        if r.get("items"):
            all_items.extend(r["items"])
        source_results.append(r)

    # [4] 新浪汽车
    if include_sina:
        r = collect_sina(date=date_param, end_date=end_param)
        if r.get("items"):
            all_items.extend(r["items"])
        source_results.append(r)

    # [5] 汽车之家
    if include_autohome:
        r = collect_autohome(date=date_param, end_date=end_param)
        if r.get("items"):
            all_items.extend(r["items"])
        source_results.append(r)

    # [6] 易车
    if include_yiche:
        r = collect_yiche(date=date_param, end_date=end_param, cookie=yiche_cookie)
        if r.get("items"):
            all_items.extend(r["items"])
        source_results.append(r)

    # ---- 去重 + 日期过滤 ----
    try:
        deduped = []
        seen_keys = set()
        for item in all_items:
            st = item.get("source_type", "")
            if st in ("cls_auto_morning", "jiemian_auto_morning", "autoinfo"):
                key = item.get("title", "") + "|" + st
            else:
                key = item.get("url", "") or item.get("title", "")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)
        all_items = deduped
        all_items = filter_items_by_date(all_items, date_param, end_param, strict=True)
    except Exception:
        pass

    # ---- 历史去重过滤 ----
    if mode == "auto" and seen_urls:
        before = len(all_items)
        all_items = [i for i in all_items if i.get("url") and i["url"] not in seen_urls]
        dedup_count = before - len(all_items)
        if dedup_count:
            source_results.append({"name": "去重过滤", "ok": True, "count": dedup_count, "detail": "已采集过，跳过"})

    # ---- 保存历史URL ----
    if mode == "auto":
        new_urls = [i["url"] for i in all_items if i.get("url")]
        seen_urls.update(new_urls)
        try:
            with open(dedup_file, "w", encoding="utf-8") as f:
                json.dump(list(seen_urls), f, ensure_ascii=False)
        except Exception:
            pass

    table_rows = items_to_table_rows(all_items, default_date=date_param, resolve_links=False)

    return {
        "ok": True,
        "date": date_param,
        "end_date": end_param,
        "count": len(all_items),
        "items": all_items,
        "sources": source_results,
        "table_columns": TABLE_COLUMNS,
        "table_rows": table_rows,
    }


# ============================================================
# 各信源独立工具（可单独在EAI中调用）
# ============================================================

@mcp.tool()
def collect_autoinfo(
    date: str, end_date: str = None
) -> dict:
    """采集中国汽车工业信息网政策新闻，自动搜索对应新闻页链接"""
    end = end_date or date
    try:
        auto_items = _collect_autoinfo_api(date, end)
        if auto_items:
            for item in auto_items:
                old_url = item.get("url", "")
                if old_url and ".gov.cn" in old_url:
                    item["url"] = ""
                # 1) 通用搜索
                info = resolve_original_link(item, search_if_needed=True)
                if info.get("is_original_link") and info.get("url"):
                    item["url"] = info["url"]
                    item["link_source"] = info.get("link_type", "搜索→原文")
                elif old_url:
                    # 2) 通用搜索失败，用百度搜索 site:{domain} {title}
                    try:
                        src = item.get("source", "")
                        for name, domain in GOV_DOMAINS.items():
                            if name in src:
                                q = f"site:{domain} {item['title'][:40]}"
                                sr = web_search(q, limit=8)
                                for r in sr.get("results", []):
                                    u = r.get("url", "")
                                    if domain in u and len(u) > 30 and "baidu.com/link" in u:
                                        item["url"] = u
                                        item["link_source"] = f"百度({domain})"
                                        break
                                break
                    except Exception:
                        pass
                    if not item.get("url"):
                        item["url"] = old_url  # 兜底：官网首页
        return {"ok": True, "source": "中国汽车工业信息网", "count": len(auto_items), "items": auto_items}
    except Exception as e:
        return {"ok": False, "source": "中国汽车工业信息网", "error": str(e)[:200]}


@mcp.tool()
def collect_cls(
    date: str, end_date: str = None
) -> dict:
    """采集财联社汽车早报，自动为每条新闻搜索独立链接"""
    end = end_date or date
    try:
        r = collect_cls_auto_morning(date, end, timeout=20)
        items = r.get("items", [])
        if items:
            for item in items:
                old_url = item.get("url", "")
                info = resolve_original_link(item, search_if_needed=True)
                if info.get("is_original_link") and info.get("url"):
                    item["url"] = info["url"]
                    item["link_source"] = info.get("link_type", "搜索→原文")
        return {"ok": True, "source": "财联社汽车早报", "count": len(items), "items": items}
    except Exception as e:
        return {"ok": False, "source": "财联社汽车早报", "error": str(e)[:200]}


@mcp.tool()
def collect_jiemian(
    date: str, end_date: str = None
) -> dict:
    """采集界面新闻汽车早报，自动搜索独立链接"""
    end = end_date or date
    try:
        items = _collect_jiemian_inline(date, end)
        if items:
            for item in items:
                info = resolve_original_link(item, search_if_needed=True)
                if info.get("is_original_link") and info.get("url"):
                    item["url"] = info["url"]
                    item["link_source"] = info.get("link_type", "搜索→原文")
        return {"ok": True, "source": "界面新闻汽车早报", "count": len(items), "items": items}
    except Exception as e:
        return {"ok": False, "source": "界面新闻汽车早报", "error": str(e)[:200]}

@mcp.tool()
def collect_sina(
    date: str, end_date: str = None
) -> dict:
    """采集新浪汽车7x24快讯"""
    end = end_date or date
    try:
        r = collect_sina_auto_7x24(date, end, pages=12, limit=20, timeout=20)
        return {"ok": r.get("ok", False), "source": "新浪汽车7x24快讯", "count": len(r.get("items", [])), "items": r.get("items", [])}
    except Exception as e:
        return {"ok": False, "source": "新浪汽车7x24快讯", "error": str(e)[:200]}


@mcp.tool()
def collect_autohome(
    date: str, end_date: str = None
) -> dict:
    """采集汽车之家上市新车"""
    end = end_date or date
    try:
        r = collect_autohome_newbrand(date, end, timeout=20, link_limit=80, fetch_details=True)
        return {"ok": r.get("ok", False), "source": "汽车之家上市新车", "count": len(r.get("items", [])), "items": r.get("items", [])}
    except Exception as e:
        return {"ok": False, "source": "汽车之家上市新车", "error": str(e)[:200]}


@mcp.tool()
def set_yiche_cookie(cookie: str) -> str:
    """设置易车Cookie（全局生效，后续collect_yiche自动使用）。在EAI调一次即可。"""
    global _yiche_cookie_global
    _yiche_cookie_global = cookie
    return f"✅ 易车Cookie已设置（{len(cookie)} bytes）"


@mcp.tool()
def collect_yiche(
    date: str, end_date: str = None, cookie: str = None
) -> dict:
    """采集易车新车消息。cookie可选，不传则使用set_yiche_cookie设置的全局Cookie。"""
    end = end_date or date
    # 优先用参数cookie，其次用全局cookie
    used_cookie = cookie or _yiche_cookie_global
    try:
        items = _collect_yiche_xinchexiaoxi(date, end, cookies=used_cookie)
        if items:
            return {"ok": True, "source": "易车新车消息", "method": "xinchexiaoxi", "count": len(items), "items": items}
        sd = datetime.date.fromisoformat(date)
        ed = datetime.date.fromisoformat(end)
        pw_items = run_coro_sync(_yiche_pw(sd, ed, cookies=used_cookie))
        if pw_items:
            return {"ok": True, "source": "易车新车新闻", "method": "playwright", "count": len(pw_items), "items": pw_items}
        return {"ok": False, "source": "易车新车新闻", "error": "xinchexiaoxi无数据且Playwright不可用"}
    except Exception as e:
        return {"ok": False, "source": "易车新车新闻", "error": str(e)[:200]}


