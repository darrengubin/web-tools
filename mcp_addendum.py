"""
MCP 增强模块 — 6大信源专用采集器
逐个调用 mcp_web_tools.py 中每个信源的专用采集方法。
"""

import datetime, time
from typing import Optional

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
        seen_urls = set()

        for page in range(1, 4):
            resp = req.get(f"https://m.jiemian.com/lists/51_{page}.html", headers=hdrs, timeout=15)
            soup = BeautifulSoup(resp.text, "lxml")

            # 找到所有新闻条目容器——每个 div.news-view 包含链接和时间
            for news_div in soup.find_all("div", class_=re.compile(r"news-view|news-left")):
                link = news_div.find("a")
                time_span = news_div.find("span")
                if not link or not time_span:
                    continue
                href = link.get("href", "")
                title = link.get_text(strip=True)
                time_text = time_span.get_text(strip=True)

                if "汽车早报" not in title:
                    continue
                if not re.search(r"jiemian\.com/article/\d+\.html", href):
                    continue

                url = "https:" + href if href.startswith("//") else \
                      "https://m.jiemian.com" + href if href.startswith("/") else href
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # 解析时间文本 → 日期
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

                if pub_date and date <= pub_date <= end:
                    # 获取详情页内容
                    try:
                        detail = req.get(url, headers=hdrs, timeout=10)
                        detail_soup = BeautifulSoup(detail.text, "lxml")
                        detail_text = detail_soup.get_text()
                        # 尝试从详情页提取更精确的发布时间
                        for meta in detail_soup.find_all("meta"):
                            prop = meta.get("property", "") or meta.get("name", "") or ""
                            if "published_time" in prop or "pubdate" in prop.lower():
                                dt = (meta.get("content", "") or "")[:10]
                                if dt: pub_date = dt; break
                    except Exception:
                        detail_text = ""

                    # 拆分早报 → 子条目（复用现有逻辑）
                    sub_items = _split_jiemian_detail(detail_text, title, url, pub_date)
                    if not sub_items:
                        # 至少返回标题本身
                        sub_items.append({
                            "title": title, "summary": title, "url": url,
                            "source": "界面新闻汽车早报", "source_type": "jiemian_auto_morning",
                            "publish_date": pub_date, "type": "行业新闻",
                        })
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
def _collect_yiche_xinchexiaoxi(date: str, end: str) -> list:
    """采集 news.yiche.com/xinchexiaoxi/（无腾讯验证码）"""
    try:
        import requests as req
        from bs4 import BeautifulSoup
        import re, datetime

        hdrs = {"User-Agent": "Mozilla/5.0 Chrome/126.0.0.0"}
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
    resolve_links: bool = True,
    include_yiche: bool = True,
    yiche_cookie: str = None,
    include_autoinfo: bool = True,
    include_cls: bool = True,
    include_jiemian: bool = True,
    include_sina: bool = True,
    include_autohome: bool = True,
) -> dict:
    """
    6大信源统一采集 + 链接补充 + 自动时间窗口 + 去重

    每个信源使用其专属采集方法：
    - 中国汽车工业信息网 → collect_autoinfo_policy_by_api
    - 财联社汽车早报    → collect_cls_auto_morning
    - 界面新闻汽车早报  → collect_jiemian_auto_morning
    - 新浪汽车7x24快讯  → collect_sina_auto_7x24
    - 汽车之家上市新车  → collect_autohome_newbrand
    - 易车新车新闻      → Playwright 无头浏览器

    链接优先级：公众号 > 官方微博 > 垂媒官网 > 百度搜索 > 新浪财经
    自动排除：拼盘/聚合类（MSN、百家号、今日头条）和搜索页链接

    时间窗口规则（mode="auto" 时）：
    - 平时：前一天12:00 → 今天12:00
    - 周一：上周五12:00 → 周一12:00

    Args:
        date: 手动模式指定日期 "2026-07-29"。mode="auto"时可不传。
        end_date: 手动模式结束日期。
        mode: "auto"(自动时间窗口) 或 "manual"(指定日期)
        resolve_links: 是否补充链接
        include_yiche: 是否采易车（需 Playwright）
        include_autoinfo: 是否采中国汽车工业信息网
        include_cls: 是否采财联社
        include_jiemian: 是否采界面新闻
        include_sina: 是否采新浪汽车
        include_autohome: 是否采汽车之家

    Returns:
        包含 items(新闻列表)、sources(各信源状态)、count(总数) 的 dict
    """
    # ---- 时间窗口计算 ----
    now = datetime.datetime.now()
    today = now.date()

    if mode == "auto":
        if now.hour < 12:
            # 中午前：采集窗口为 昨天12:00 → 今天12:00
            start_date = today - datetime.timedelta(days=1)
            end_date_calc = today
            if today.weekday() == 0:  # 周一：延伸到上周五
                start_date = today - datetime.timedelta(days=3)
        else:
            # 中午后：采集窗口为 今天12:00 → 明天12:00
            start_date = today
            end_date_calc = today + datetime.timedelta(days=1)

        start_str = start_date.isoformat()
        end_str = end_date_calc.isoformat()
    else:
        start_str = date
        end_str = end_date or date

    date = start_str
    end = end_str

    # ---- 读取历史去重 ----
    dedup_file = os.path.join(here, ".collected_urls.json")
    seen_urls = set()
    if os.path.exists(dedup_file):
        try:
            with open(dedup_file, "r", encoding="utf-8") as f:
                seen_urls = set(json.load(f))
        except Exception:
            seen_urls = set()

    items = []
    sources = []

    # [1] 中国汽车工业信息网 — 内联 API 采集（替代复杂的 collect_autoinfo_policy_by_api）
    if include_autoinfo:
        try:
            auto_items = _collect_autoinfo_api(date, end)
            if auto_items:
                items.extend(auto_items)
            sources.append({
                "name": "中国汽车工业信息网",
                "ok": True,
                "count": len(auto_items),
                "url": "https://www.autoinfo.org.cn/",
            })
        except Exception as e:
            sources.append({"name": "中国汽车工业信息网", "ok": False, "error": str(e)[:80]})

    # [2] 财联社汽车早报 — 专用采集
    if include_cls:
        try:
            r = collect_cls_auto_morning(date, end, timeout=20)
            if r.get("items"):
                items.extend(r["items"])
            sources.append({
                "name": "财联社汽车早报",
                "ok": r.get("ok", False),
                "count": len(r.get("items", [])),
                "url": "https://www.cls.cn/subject/7527",
            })
        except Exception as e:
            sources.append({"name": "财联社汽车早报", "ok": False, "error": str(e)[:80]})

    # [3] 界面新闻汽车早报 — 内联采集（修复日期匹配问题）
    if include_jiemian:
        try:
            jiemian_items = _collect_jiemian_inline(date, end)
            if jiemian_items:
                items.extend(jiemian_items)
            sources.append({
                "name": "界面新闻汽车早报",
                "ok": True,
                "count": len(jiemian_items),
                "url": "https://m.jiemian.com/lists/51_1.html",
            })
        except Exception as e:
            sources.append({"name": "界面新闻汽车早报", "ok": False, "error": str(e)[:80]})

    # [4] 新浪汽车7x24快讯 — 专用采集
    if include_sina:
        try:
            r = collect_sina_auto_7x24(date, end, pages=12, limit=20, timeout=20)
            if r.get("items"):
                items.extend(r["items"])
            sources.append({
                "name": "新浪汽车7x24快讯",
                "ok": r.get("ok", False),
                "count": len(r.get("items", [])),
                "url": "https://auto.sina.com.cn/7x24/",
            })
        except Exception as e:
            sources.append({"name": "新浪汽车7x24快讯", "ok": False, "error": str(e)[:80]})

    # [5] 汽车之家上市新车 — 专用采集
    if include_autohome:
        try:
            r = collect_autohome_newbrand(date, end, timeout=20, link_limit=80, fetch_details=True)
            if r.get("items"):
                items.extend(r["items"])
            sources.append({
                "name": "汽车之家上市新车",
                "ok": r.get("ok", False),
                "count": len(r.get("items", [])),
                "url": "https://www.autohome.com.cn/newbrand/",
            })
        except Exception as e:
            sources.append({"name": "汽车之家上市新车", "ok": False, "error": str(e)[:80]})

    # [6] 易车新车消息 — 优先用 xinchexiaoxi（无验证码），失败再用 Playwright
    if include_yiche:
        yiche_items = []
        # 先尝试 xinchexiaoxi（纯requests，无验证码）
        try:
            yiche_items = _collect_yiche_xinchexiaoxi(date, end)
        except Exception:
            pass

        if yiche_items:
            items.extend(yiche_items)
            sources.append({
                "name": "易车新车消息",
                "ok": True,
                "count": len(yiche_items),
                "url": "https://news.yiche.com/xinchexiaoxi/",
            })
        else:
            # 降级到 Playwright（需处理验证码）
            try:
                sd = datetime.date.fromisoformat(date)
                ed = datetime.date.fromisoformat(end)
                for attempt in range(2):
                    try:
                        pw_items = run_coro_sync(_yiche_pw(sd, ed, cookies=yiche_cookie))
                        if pw_items:
                            items.extend(pw_items)
                            sources.append({
                                "name": "易车新车新闻(Playwright)",
                                "ok": True,
                                "count": len(pw_items),
                                "url": "https://news.yiche.com/xinche/",
                            })
                            break
                    except Exception:
                        if attempt < 1:
                            time.sleep(1.5)
                        else:
                            raise
                else:
                    sources.append({
                        "name": "易车新车新闻", "ok": False,
                        "error": "xinchexiaoxi无数据且Playwright不可用",
                    })
            except Exception as e:
                sources.append({"name": "易车新车新闻", "ok": False, "error": str(e)[:80]})

    # ---- 去重 + 日期过滤 ----
    # 注意：CLS/界面子条目共享同一URL，不能按URL去重，改用(title+source_type)去重
    try:
        deduped = []
        seen_keys = set()
        for item in items:
            st = item.get("source_type", "")
            if st in ("cls_auto_morning", "jiemian_auto_morning", "autoinfo"):
                key = item.get("title", "") + "|" + st
            else:
                key = item.get("url", "") or item.get("title", "")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)
        items = deduped
        items = filter_items_by_date(items, date, end, strict=True)
    except Exception:
        pass

    # ---- 链接补充 ----
    # 1) 早报/政策子条目搜索独立链接
    # 2) 政府类补官网域名（兜底）
    if resolve_links and items:
        # 先做搜索（对cls/jiemian/autoinfo等没有独立链接的信源）
        try:
            for item in items:
                st = item.get("source_type", "")
                if st in ("cls_auto_morning", "jiemian_auto_morning", "autoinfo"):
                    # 清除首页域名，让 resolve_original_link 触发搜索
                    old_url = item.get("url", "")
                    if old_url and ".gov.cn" in old_url:
                        item["url"] = ""
                    info = resolve_original_link(item, search_if_needed=True)
                    if info.get("is_original_link") and info.get("url"):
                        item["url"] = info["url"]
                        item["link_source"] = info.get("link_type", "搜索→原文")
                    elif old_url:
                        item["url"] = old_url  # 搜索失败则恢复首页兜底
        except Exception:
            pass

        # 再补官网域名（仅对仍然无链接的条目）
        for item in items:
            if not item.get("url"):
                src = item.get("source", "") or item.get("type", "") or ""
                url, link_src = _gov_link(src)
                if url:
                    item["url"] = url
                    item["link_source"] = link_src

    # ---- 历史去重：过滤已采集过的URL ----
    dedup_count = 0
    if mode == "auto" and seen_urls:
        before = len(items)
        items = [i for i in items if i.get("url") and i["url"] not in seen_urls]
        dedup_count = before - len(items)
        if dedup_count:
            sources.append({
                "name": "去重过滤", "ok": True,
                "count": dedup_count, "detail": "已采集过，跳过",
            })

    # ---- 保存新采集的URL到历史文件 ----
    if mode == "auto":
        new_urls = [i["url"] for i in items if i.get("url")]
        seen_urls.update(new_urls)
        try:
            with open(dedup_file, "w", encoding="utf-8") as f:
                json.dump(list(seen_urls), f, ensure_ascii=False)
        except Exception:
            pass

    table_rows = items_to_table_rows(items, default_date=date, resolve_links=False)

    return {
        "ok": True,
        "date": date,
        "end_date": end,
        "count": len(items),
        "items": items,
        "sources": sources,
        "table_columns": TABLE_COLUMNS,
        "table_rows": table_rows,
    }
