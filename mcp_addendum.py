"""
MCP 增强模块 — 追加到 mcp_web_tools.py 尾部
"""
import os, re, json, time, datetime
from typing import Optional

# ============================================================
# 政府机构 → 官网域名映射
# ============================================================
GOV_WEBSITE_DOMAINS = {
    "工业和信息化部": "miit.gov.cn", "工信部": "miit.gov.cn",
    "装备工业一司": "miit.gov.cn", "节能与综合利用司": "miit.gov.cn",
    "国家发展改革委": "ndrc.gov.cn", "发改委": "ndrc.gov.cn", "发展改革委": "ndrc.gov.cn",
    "国家能源局": "nea.gov.cn", "商务部": "mofcom.gov.cn",
    "交通运输部": "mot.gov.cn", "国家市场监管总局": "samr.gov.cn",
    "财政部": "mof.gov.cn", "科技部": "most.gov.cn",
    "生态环境部": "mee.gov.cn", "公安部": "mps.gov.cn",
    "国家统计局": "stats.gov.cn", "中国汽车工业协会": "caam.org.cn",
    "国务院": "gov.cn",
    "北京": "beijing.gov.cn", "上海": "shanghai.gov.cn",
    "天津": "tj.gov.cn", "重庆": "cq.gov.cn",
    "广东": "gd.gov.cn", "深圳": "sz.gov.cn", "广州": "gz.gov.cn",
    "清远": "qingyuan.gov.cn", "湛江": "zhanjiang.gov.cn",
    "浙江": "zj.gov.cn", "杭州": "hangzhou.gov.cn",
    "江苏": "jiangsu.gov.cn", "安徽": "ah.gov.cn", "合肥": "hefei.gov.cn",
    "四川": "sc.gov.cn", "成都": "chengdu.gov.cn",
    "湖北": "hubei.gov.cn", "随州": "suizhou.gov.cn",
    "云南": "yn.gov.cn", "西双版纳": "xsbn.gov.cn",
    "福建": "fj.gov.cn", "山东": "shandong.gov.cn", "济南": "jinan.gov.cn",
    "湖南": "hunan.gov.cn", "江西": "jiangxi.gov.cn", "南昌": "nc.gov.cn",
    "贵州": "guizhou.gov.cn", "广西": "gxzf.gov.cn",
    "陕西": "shaanxi.gov.cn", "甘肃": "gansu.gov.cn", "青海": "qinghai.gov.cn",
    "宁夏": "nx.gov.cn", "新疆": "xinjiang.gov.cn", "海南": "hainan.gov.cn",
    "西藏": "xizang.gov.cn", "河南": "henan.gov.cn", "河北": "hebei.gov.cn",
    "山西": "shanxi.gov.cn", "辽宁": "ln.gov.cn", "吉林": "jl.gov.cn",
    "黑龙江": "hlj.gov.cn",
}


def get_gov_domain(source: str) -> Optional[str]:
    for name, domain in GOV_WEBSITE_DOMAINS.items():
        if name in source:
            return domain
    return None


# 聚合/拼盘 URL 排除
AGGREGATOR_PATTERNS = [
    "msn.cn", "msn.com", "baijiahao.baidu.com", "toutiao.com",
    "hao123.com", "k.sina.com.cn", "qianzhan.com",
    "baidu.com/s", "so.com/s", "sogou.com/web",
]


def is_aggregator(url: str) -> bool:
    url_l = url.lower()
    for p in AGGREGATOR_PATTERNS:
        if p in url_l:
            return True
    return False


def is_article_link(url: str) -> bool:
    if not url or len(url) < 15 or is_aggregator(url):
        return False
    if "baidu.com/link" in url.lower():
        return True
    path = url.split("/")[3:] if len(url.split("/")) > 3 else []
    return bool(path and len("/".join(path)) > 10)


@mcp.tool()
def collect_all_sources(
    date: str, end_date: str = None, resolve_links: bool = True,
    include_yiche: bool = True, include_autoinfo: bool = True,
    include_cls: bool = True, include_jiemian: bool = True,
    include_sina: bool = True, include_autohome: bool = True,
    include_new_car: bool = True,
    autoinfo_max_pages: int = 5, yiche_timeout: int = 30000,
) -> dict:
    """全信源统一采集 + 链接补充"""
    end = end_date or date
    all_items = []
    source_results = []

    # 构建 batch 配置
    batch_sources = []
    if include_cls:
        batch_sources.append({"name": "财联社汽车早报", "type": "cls_auto_morning", "timeout": 20})
    if include_jiemian:
        batch_sources.append({"name": "界面新闻汽车早报", "type": "jiemian_auto_morning", "timeout": 20, "max_candidates": 30})
    if include_sina:
        batch_sources.append({"name": "新浪汽车7x24快讯", "type": "sina_auto_7x24", "pages": 12, "limit": 20})
    if include_autohome:
        batch_sources.append({"name": "汽车之家上市新车", "type": "autohome_newbrand", "timeout": 20, "link_limit": 80, "fetch_details": True})
    if include_new_car:
        batch_sources.append({"name": "新车上市聚合", "type": "new_car_launches", "timeout": 20, "limit": 20})

    if batch_sources:
        try:
            batch = batch_collect_sources(
                sources=batch_sources, start_date=date, end_date=end,
                default_dynamic=False, dedupe=True, strict_date=True,
                resolve_original_links=False,
            )
            if batch.get("ok"):
                all_items.extend(batch.get("items", []))
            for sr in batch.get("sources", []):
                source_results.append(sr)
        except Exception as e:
            source_results.append({"name": "batch", "ok": False, "error": str(e)[:100]})

    # autoinfo - 直接调 API，不需要外部模块
    if include_autoinfo:
        try:
            import requests as req
            base_api = "https://www.autoinfo.org.cn/prod-api"
            api_headers = {"User-Agent": "Mozilla/5.0 Chrome/126.0.0.0",
                           "Accept": "application/json", "Referer": "https://www.autoinfo.org.cn/"}
            auto_items = []
            for api_path, params, label in [
                ("/api/policy/ttPolicy/newPolicy", {"flag": "0"}, "最新政策"),
                ("/api/policy/ttPolicyReport/policyReport", {}, "政策报道"),
                ("/api/policy/ttPolicyInterpret/localOriginal", {"unscrambleUnit": "1"}, "最新原创"),
                ("/api/policy/ttPolicyInterpret/policyExplain", {}, "政策解读"),
            ]:
                q = {**params, "pageNum": 1, "pageSize": 50}
                r = req.get(base_api + api_path, params=q, headers=api_headers, timeout=15)
                if r.status_code != 200:
                    continue
                d = r.json()
                if d.get("code") != 200:
                    continue
                for row in d.get("data", []):
                    pub = str(row.get("publishDate") or row.get("publicDate") or "")[:10]
                    if pub != date:
                        continue
                    src = row.get("source") or row.get("policyResource") or label
                    title = row.get("title","") or ""
                    summary = row.get("summary") or row.get("introduction") or row.get("mainPoint") or ""
                    gov_domain = get_gov_domain(src)
                    auto_items.append({
                        "title": title, "source": src, "type": label,
                        "publish_date": pub, "summary": summary[:500] if summary else "",
                        "url": f"https://www.{gov_domain}" if gov_domain else "",
                        "link_source": f"官网({gov_domain})" if gov_domain else "",
                        "source_type": "autoinfo",
                    })
            all_items.extend(auto_items)
            source_results.append({"name": "中国汽车工业信息网", "ok": True, "count": len(auto_items), "type": "autoinfo"})
        except Exception as e:
            source_results.append({"name": "中国汽车工业信息网", "ok": False, "error": str(e)[:80], "type": "autoinfo"})

    # yiche (Playwright, 可能在 Render 上失败)
    if include_yiche:
        try:
            yr = collect_yiche_news_playwright(start_date=date, end_date=end, timeout=yiche_timeout, max_scroll=3, retries=1)
            if yr.get("items"):
                all_items.extend(yr["items"])
            source_results.append({"name": "易车新车", "ok": yr.get("ok", False), "count": yr.get("count", 0), "type": "yiche"})
        except Exception as e:
            source_results.append({"name": "易车新车", "ok": False, "error": str(e)[:80], "type": "yiche"})

    # 去重
    try:
        all_items = dedupe_items(all_items)
        all_items = filter_items_by_date(all_items, date, end, strict=True)
    except Exception:
        pass

    # 链接补充
    if resolve_links and all_items:
        try:
            for item in all_items:
                title = item.get("title", "") or ""
                source = item.get("source", "") or ""
                source_type = item.get("source_type", "") or ""
                current_url = item.get("url", "") or ""

                gov_domain = get_gov_domain(source)
                if gov_domain and is_article_link(current_url):
                    continue  # 已有好链接
                if gov_domain and not current_url:
                    item["url"] = f"https://www.{gov_domain}"
                    item["link_source"] = f"官网({gov_domain})"
        except Exception:
            pass

    table_rows = items_to_table_rows(all_items, default_date=date, resolve_links=False)

    return {
        "ok": True, "date": date, "end_date": end,
        "sources": source_results, "count": len(all_items), "items": all_items,
        "table_columns": TABLE_COLUMNS, "table_rows": table_rows,
    }


# ============================================================
# 易车 Playwright 采集器（被 collect_all_sources 引用）
# ============================================================

async def _collect_yiche_pw(start_date, end_date, timeout=30000, max_scroll=3):
    """Playwright 绕过易车验证码"""
    items = []
    source_url = "https://news.yiche.com/xinche/"
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}, locale="zh-CN")
            page = await ctx.new_page()
            await page.goto(source_url, wait_until="networkidle", timeout=timeout)
            await page.wait_for_timeout(3000)
            for _ in range(max_scroll):
                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(1500)
            links = await page.eval_on_selector_all("a[href*='news.yiche.com']",
                "els => els.map(el => ({href: el.href, text: el.textContent.trim()}))")
            seen = set()
            for link in links:
                title = link.get("text","").strip()
                url = link.get("href","")
                if not title or not url or url in seen or len(title) < 8:
                    continue
                seen.add(url)
                for d in range((end_date - start_date).days + 1):
                    day = start_date + datetime.timedelta(days=d)
                    ds = f"{day.month}月{day.day}日"
                    if ds in title:
                        items.append({
                            "title": title[:120], "summary": title, "url": url,
                            "source": "易车新车", "source_type": "yiche_news_direct",
                            "date": day.isoformat(), "category": "产品信息",
                            "confidence": "high", "date_confidence": "high",
                        })
                        break
            await ctx.close()
            await browser.close()
    except Exception as e:
        pass  # 静默失败
    return items


def collect_yiche_news_playwright(start_date: str, end_date: str = None, timeout: int = 30000, max_scroll: int = 3, retries: int = 1) -> dict:
    """采集易车新车新闻（Playwright绕过验证码）"""
    end_date = end_date or start_date
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    for attempt in range(retries + 1):
        try:
            items = run_coro_sync(_collect_yiche_pw(start, end, timeout, max_scroll))
            if items:
                return {"ok": True, "source": "易车新车", "source_url": "https://news.yiche.com/xinche/",
                        "count": len(items), "items": items}
        except Exception:
            if attempt < retries:
                time.sleep(1.5)
    return {"ok": False, "source": "易车新车", "count": 0, "items": [],
            "error_type": "playwright_failed", "message": "易车触发验证码或 Playwright 不可用"}
