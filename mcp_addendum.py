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
# 易车 Playwright 采集器
# ============================================================

async def _yiche_pw(start, end, timeout=30000, scroll=3):
    items = []
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}, locale="zh-CN")
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
    date: str,
    end_date: str = None,
    resolve_links: bool = True,
    include_yiche: bool = True,
    include_autoinfo: bool = True,
    include_cls: bool = True,
    include_jiemian: bool = True,
    include_sina: bool = True,
    include_autohome: bool = True,
) -> dict:
    """
    6大信源统一采集 + 链接补充

    每个信源使用其专属采集方法：
    - 中国汽车工业信息网 → collect_autoinfo_policy_by_api
    - 财联社汽车早报    → collect_cls_auto_morning
    - 界面新闻汽车早报  → collect_jiemian_auto_morning
    - 新浪汽车7x24快讯  → collect_sina_auto_7x24
    - 汽车之家上市新车  → collect_autohome_newbrand
    - 易车新车新闻      → Playwright 无头浏览器

    链接策略（按优先级）：
    - 政府新闻 → source 匹配 GOV_DOMAINS → 官网域名
    - 垂媒新闻 → 各采集器已自带回独立文章链接
    - 拼盘/搜索页链接 → 自动排除

    Args:
        date: 采集日期 "2026-07-24"
        end_date: 结束日期，不传则采当天
        resolve_links: 是否补充链接
        include_yiche: 是否采易车（需 Playwright）
        include_autoinfo: 是否采中国汽车工业信息网
        include_cls: 是否采财联社
        include_jiemian: 是否采界面新闻
        include_sina: 是否采新浪汽车
        include_autohome: 是否采汽车之家
    """
    end = end_date or date
    items = []
    sources = []

    # [1] 中国汽车工业信息网 — 专用 API 采集
    if include_autoinfo:
        try:
            r = collect_autoinfo_policy_by_api(date, end, pages=8, page_size=50)
            if r.get("items"):
                for item in r["items"]:
                    src = item.get("source", "") or item.get("type", "")
                    url, link_src = _gov_link(src)
                    if url:
                        item["url"] = url
                        item["link_source"] = link_src
                items.extend(r["items"])
            sources.append({
                "name": "中国汽车工业信息网",
                "ok": r.get("ok", False),
                "count": len(r.get("items", [])),
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

    # [3] 界面新闻汽车早报 — 专用采集
    if include_jiemian:
        try:
            r = collect_jiemian_auto_morning(date, end, timeout=20, max_candidates=30)
            if r.get("items"):
                items.extend(r["items"])
            sources.append({
                "name": "界面新闻汽车早报",
                "ok": r.get("ok", False),
                "count": len(r.get("items", [])),
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

    # [6] 易车新车新闻 — Playwright 无头浏览器
    if include_yiche:
        try:
            sd = datetime.date.fromisoformat(date)
            ed = datetime.date.fromisoformat(end)
            for attempt in range(2):
                try:
                    yiche_items = run_coro_sync(_yiche_pw(sd, ed))
                    if yiche_items:
                        items.extend(yiche_items)
                        sources.append({
                            "name": "易车新车新闻",
                            "ok": True,
                            "count": len(yiche_items),
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
                    "error": "Playwright 不可用或验证码拦截",
                })
        except Exception as e:
            sources.append({"name": "易车新车新闻", "ok": False, "error": str(e)[:80]})

    # ---- 去重 + 日期过滤 ----
    try:
        items = dedupe_items(items)
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
