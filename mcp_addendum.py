"""
MCP 增强模块 — 6大信源采集器
"""
import os, re, json, time, datetime
from typing import Optional

# ============================================================
# 政府机构 → 官网域名映射（用于 autoinfo 链接补充）
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


AGGREGATOR_URLS = ["msn.cn", "baijiahao", "toutiao.com", "hao123.com",
                   "baidu.com/s", "so.com/s", "sogou.com/web"]


def is_article_url(url: str) -> bool:
    if not url or len(url) < 15:
        return False
    url_l = url.lower()
    for p in AGGREGATOR_URLS:
        if p in url_l:
            return False
    return True


# ============================================================
# 易车 Playwright 采集器
# ============================================================

async def _yiche_playwright(start_date, end_date, timeout=30000, max_scroll=3):
    """Playwright 绕过易车验证码，采集新车新闻"""
    items = []
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}, locale="zh-CN"
            )
            page = await ctx.new_page()
            await page.goto("https://news.yiche.com/xinche/", wait_until="networkidle", timeout=timeout)
            await page.wait_for_timeout(3000)
            for _ in range(max_scroll):
                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(1500)
            links = await page.eval_on_selector_all(
                "a[href*='news.yiche.com']",
                "els => els.map(el => ({href: el.href, text: el.textContent.trim()}))"
            )
            seen = set()
            for link in links:
                title = (link.get("text") or "").strip()
                url = link.get("href") or ""
                if not title or not url or url in seen or len(title) < 8:
                    continue
                seen.add(url)
                for d in range((end_date - start_date).days + 1):
                    day = start_date + datetime.timedelta(days=d)
                    ds = f"{day.month}月{day.day}日"
                    if ds in title:
                        items.append({
                            "title": title[:120], "summary": title,
                            "url": url, "source": "易车新车",
                            "source_type": "yiche_news_direct",
                            "publish_date": day.isoformat(),
                            "type": "产品信息",
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
) -> dict:
    """6大信源统一采集 + 链接补充

    **信源清单：**
    1. 中国汽车工业信息网 — https://www.autoinfo.org.cn/#/policy/dynamic/index
    2. 财联社汽车早报 — https://www.cls.cn/subject/7527
    3. 界面新闻汽车早报 — https://m.jiemian.com/lists/51_1.html
    4. 新浪汽车7x24快讯 — https://auto.sina.com.cn/7x24/?tagid=1
    5. 汽车之家上市新车 — https://www.autohome.com.cn/newbrand/
    6. 易车新车新闻 — https://news.yiche.com/xinche/

    Args:
        date: 采集日期，如 "2026-07-24"
        end_date: 结束日期，不传则只采当天
        resolve_links: 是否补充链接
        include_yiche: 是否采集易车（需Playwright环境）
        include_autoinfo: 是否采集中国汽车工业信息网

    Returns:
        包含 items(新闻列表)、sources(各信源状态)、count(总数) 的 dict
    """
    end = end_date or date
    all_items = []
    source_results = []

    # ---- 信源A: 中国汽车工业信息网（API直调） ----
    if include_autoinfo:
        try:
            import requests as req
            base = "https://www.autoinfo.org.cn/prod-api"
            headers = {
                "User-Agent": "Mozilla/5.0 Chrome/126.0.0.0",
                "Accept": "application/json",
                "Referer": "https://www.autoinfo.org.cn/",
            }
            auto_items = []
            for api_path, api_params, label in [
                ("/api/policy/ttPolicy/newPolicy", {"flag": "0"}, "最新政策"),
                ("/api/policy/ttPolicyReport/policyReport", {}, "政策报道"),
                ("/api/policy/ttPolicyInterpret/localOriginal", {"unscrambleUnit": "1"}, "最新原创"),
                ("/api/policy/ttPolicyInterpret/policyExplain", {}, "政策解读"),
            ]:
                q = {**api_params, "pageNum": 1, "pageSize": 50}
                r = req.get(base + api_path, params=q, headers=headers, timeout=15)
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
                    title = (row.get("title") or "")[:200]
                    summary = row.get("summary") or row.get("introduction") or row.get("mainPoint") or ""
                    gov = get_gov_domain(src)
                    auto_items.append({
                        "title": title, "source": src, "type": label,
                        "publish_date": pub,
                        "summary": (summary or "")[:500],
                        "url": f"https://www.{gov}" if gov else "",
                        "link_source": f"官网({gov})" if gov else "",
                        "source_type": "autoinfo",
                    })
            all_items.extend(auto_items)
            source_results.append({
                "name": "中国汽车工业信息网", "ok": True,
                "count": len(auto_items), "url": "https://www.autoinfo.org.cn/",
            })
        except Exception as e:
            source_results.append({
                "name": "中国汽车工业信息网", "ok": False,
                "error": str(e)[:80],
            })

    # ---- 信源B/C/D/E: 财联社/界面/新浪/汽车之家（batch_collect_sources） ----
    try:
        batch = batch_collect_sources(
            sources=[
                {"name": "财联社汽车早报", "type": "cls_auto_morning", "timeout": 20},
                {"name": "界面新闻汽车早报", "type": "jiemian_auto_morning", "timeout": 20, "max_candidates": 30},
                {"name": "新浪汽车7x24快讯", "type": "sina_auto_7x24", "pages": 12, "limit": 20},
                {"name": "汽车之家上市新车", "type": "autohome_newbrand", "timeout": 20, "link_limit": 80, "fetch_details": True},
            ],
            start_date=date, end_date=end, default_dynamic=False,
            dedupe=True, strict_date=True, resolve_original_links=False,
        )
        if batch.get("ok"):
            all_items.extend(batch.get("items", []))
        for sr in batch.get("sources", []):
            source_results.append(sr)
    except Exception as e:
        source_results.append({"name": "批量信源(财联社/界面/新浪/汽车之家)", "ok": False, "error": str(e)[:80]})

    # ---- 信源F: 易车新车（Playwright） ----
    if include_yiche:
        try:
            sd = datetime.date.fromisoformat(date)
            ed = datetime.date.fromisoformat(end)
            for attempt in range(2):
                try:
                    yiche_items = run_coro_sync(_yiche_playwright(sd, ed))
                    if yiche_items:
                        all_items.extend(yiche_items)
                        source_results.append({
                            "name": "易车新车", "ok": True,
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
                source_results.append({
                    "name": "易车新车", "ok": False,
                    "error": "Playwright 不可用或验证码拦截",
                })
        except Exception as e:
            source_results.append({
                "name": "易车新车", "ok": False, "error": str(e)[:80],
            })

    # ---- 去重 + 日期过滤 ----
    try:
        all_items = dedupe_items(all_items)
        all_items = filter_items_by_date(all_items, date, end, strict=True)
    except Exception:
        pass

    # ---- 链接补充（政府类新闻匹配官网域名） ----
    if resolve_links and all_items:
        try:
            for item in all_items:
                src = item.get("source", "") or ""
                cur_url = item.get("url", "") or ""
                gov = get_gov_domain(src)
                if gov and not cur_url:
                    item["url"] = f"https://www.{gov}"
                    item["link_source"] = f"官网({gov})"
        except Exception:
            pass

    table_rows = items_to_table_rows(all_items, default_date=date, resolve_links=False)

    return {
        "ok": True,
        "date": date,
        "end_date": end,
        "count": len(all_items),
        "items": all_items,
        "sources": source_results,
        "table_columns": TABLE_COLUMNS,
        "table_rows": table_rows,
    }
