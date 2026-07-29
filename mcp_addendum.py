"""
================================================================================
  MCP 增强模块 — 追加到 mcp_web_tools.py 尾部
  新增：
    1. collect_autoinfo_direct  — 直调 autoinfo API，无需 Playwright
    2. collect_yiche_news  — Playwright 绕过验证码采集易车新车新闻
    3. collect_all_sources  — 全信源统一采集 + 链接补充
    4. 政府网站域名映射表，用于链接优先级
================================================================================
"""

# ============================================================
# 政府机构 → 官网域名映射
# 用于链接优先级判定和补链搜索
# ============================================================
GOV_WEBSITE_DOMAINS = {
    # 部委
    "工业和信息化部":   "miit.gov.cn",
    "工信部":          "miit.gov.cn",        # ← 新增！source 中可能只写"工信部"
    "装备工业一司":     "miit.gov.cn",
    "国家发展改革委":   "ndrc.gov.cn",
    "发改委":          "ndrc.gov.cn",
    "发展改革委":      "ndrc.gov.cn",        # ← 新增
    "国家能源局":      "nea.gov.cn",
    "商务部":          "mofcom.gov.cn",
    "交通运输部":      "mot.gov.cn",
    "国家市场监管总局": "samr.gov.cn",
    "财政部":          "mof.gov.cn",
    "科技部":          "most.gov.cn",
    "生态环境部":      "mee.gov.cn",
    "公安部":          "mps.gov.cn",
    "国家统计局":      "stats.gov.cn",
    "中国汽车工业协会": "caam.org.cn",
    "国务院":          "gov.cn",
    # 省/市
    "北京":  "beijing.gov.cn",   "上海":  "shanghai.gov.cn",
    "天津":  "tj.gov.cn",       "重庆":  "cq.gov.cn",
    "广东":  "gd.gov.cn",       "深圳":  "sz.gov.cn",
    "广州":  "gz.gov.cn",       "浙江":  "zj.gov.cn",
    "杭州":  "hangzhou.gov.cn", "江苏":  "jiangsu.gov.cn",
    "安徽":  "ah.gov.cn",       "合肥":  "hefei.gov.cn",
    "四川":  "sc.gov.cn",       "成都":  "chengdu.gov.cn",
    "湖北":  "hubei.gov.cn",    "云南":  "yn.gov.cn",
    "西双版纳":"xsbn.gov.cn",    "福建":  "fj.gov.cn",
    "山东":  "shandong.gov.cn", "济南":  "jinan.gov.cn",   # ← 新增
    "湖南":  "hunan.gov.cn",    "江西":  "jiangxi.gov.cn",  # ← 新增
    "南昌":  "nc.gov.cn",       "贵州":  "guizhou.gov.cn",  # ← 新增
    "广西":  "gxzf.gov.cn",     "陕西":  "shaanxi.gov.cn",  # ← 新增
    "河南":  "henan.gov.cn",    "河北":  "hebei.gov.cn",    # ← 新增
    "山西":  "shanxi.gov.cn",   "辽宁":  "ln.gov.cn",      # ← 新增
    "吉林":  "jl.gov.cn",       "黑龙江":"hlj.gov.cn",     # ← 新增
    "甘肃":  "gansu.gov.cn",    "青海":  "qinghai.gov.cn",  # ← 新增
    "宁夏":  "nx.gov.cn",       "新疆":  "xinjiang.gov.cn", # ← 新增
    "海南":  "hainan.gov.cn",   "西藏":  "xizang.gov.cn",  # ← 新增
}


def get_gov_domain(source: str) -> Optional[str]:
    """从 source 文本中提取政府网站域名"""
    for name, domain in GOV_WEBSITE_DOMAINS.items():
        if name in source:
            return domain
    return None


# ============================================================
# 增强版 autoinfo 采集器 — 直调已知 API，无需 Playwright
# ============================================================

def collect_autoinfo_direct_by_api(
    api_path: str,
    params: dict = None,
    page_size: int = 50,
    max_pages: int = 10,
    start_date: str = None,
    end_date: str = None,
    source_label: str = "",
) -> list[dict]:
    """
    直调 autoinfo 已知 API 采集。
    返回 [{"title", "summary", "url", "source", "source_type", "date", "category", ...}]
    """
    import requests as req

    base = "https://www.autoinfo.org.cn/prod-api"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.autoinfo.org.cn/",
    }

    query_params = dict(params or {})
    query_params["pageSize"] = page_size
    items = []

    for page in range(1, max_pages + 1):
        query_params["pageNum"] = page
        try:
            resp = req.get(base + api_path, params=query_params, headers=headers, timeout=15)
            data = resp.json()
        except Exception as e:
            break

        if data.get("code") != 200:
            break

        rows = data.get("data", [])
        if not rows:
            break

        stop = False
        for row in rows:
            title = (row.get("title") or "").strip()
            if not title:
                continue

            # 日期提取
            pub_date = (
                str(row.get("publishDate") or row.get("publicDate") or "")
            )[:10]

            # 日期过滤
            if start_date and pub_date and pub_date < start_date:
                stop = True
                continue
            if end_date and pub_date and pub_date > end_date:
                continue

            # 摘要
            summary = (
                row.get("summary")
                or row.get("introduction")
                or row.get("mainPoint")
                or ""
            )
            if not summary and row.get("content"):
                summary = row["content"][:200]

            # 来源
            source = row.get("source") or row.get("policyResource") or source_label

            # 分类推断
            title_text = f"{title} {summary}"
            category = infer_category(title_text, summary or "")

            # 生成详情页 URL
            item_id = row.get("id", "")
            detail_url = f"https://www.autoinfo.org.cn/prod-api/api/policy/ttPolicy/queryContentById/{item_id}"

            items.append({
                "title": clean_text(title),
                "summary": clean_text(summary)[:500],
                "content": clean_text(row.get("content", "") or summary),
                "url": detail_url,
                "source": source or source_label,
                "source_type": "autoinfo_direct_api",
                "date": pub_date,
                "publish_date": pub_date,
                "category": category,
                "confidence": "high" if pub_date else "medium",
                "date_confidence": "high" if pub_date else "medium",
            })

        if stop or len(rows) < page_size:
            break

    return items


@mcp.tool()
def collect_autoinfo_all(
    start_date: str,
    end_date: str = None,
    max_pages: int = 5,
) -> dict:
    """
    [增强版] 直调中国汽车工业信息网已知 API 采集所有政策模块。
    无需 Playwright，无需登录，稳定高速。
    包含：最新政策、政策报道、最新原创、政策解读
    """
    end_date = end_date or start_date

    api_configs = [
        ("/api/policy/ttPolicy/newPolicy", {"flag": "0"}, "最新政策"),
        ("/api/policy/ttPolicyReport/policyReport", {}, "政策报道"),
        ("/api/policy/ttPolicyInterpret/localOriginal", {"unscrambleUnit": "1"}, "最新原创"),
        ("/api/policy/ttPolicyInterpret/policyExplain", {}, "政策解读"),
    ]

    all_items = []
    module_results = []

    for api_path, params, label in api_configs:
        items = collect_autoinfo_direct_by_api(
            api_path=api_path,
            params=params,
            page_size=50,
            max_pages=max_pages,
            start_date=start_date,
            end_date=end_date,
            source_label=label,
        )
        module_results.append({"module": label, "count": len(items)})
        all_items.extend(items)

    all_items = dedupe_items(all_items)
    all_items = filter_items_by_date(all_items, start_date, end_date, strict=True)

    # 链接补充：政府新闻 → 官网域名
    for item in all_items:
        source = item.get("source", "") or ""
        title = item.get("title", "") or ""
        gov_domain = get_gov_domain(source)
        if gov_domain:
            # 尝试搜索 exact URL
            clean_t = re.sub(r'[《》（）【】""「」\'，。；：]', '', title)[:40]
            try:
                sr = web_search(f"site:{gov_domain} {clean_t}", limit=5, retries=1)
                if sr.get("ok"):
                    for r in sr.get("results", []):
                        url = r.get("url", "")
                        if gov_domain in url and len(url) > 30:
                            item["url"] = url
                            item["link_source"] = f"官网({gov_domain})精确"
                            break
                    else:
                        item["url"] = f"https://www.{gov_domain}"
                        item["link_source"] = f"官网({gov_domain})"
                else:
                    item["url"] = f"https://www.{gov_domain}"
                    item["link_source"] = f"官网({gov_domain})"
            except Exception:
                item["url"] = f"https://www.{gov_domain}"
                item["link_source"] = f"官网({gov_domain})"

    return {
        "ok": True,
        "source": "中国汽车工业信息网(API直调)",
        "source_url": "https://www.autoinfo.org.cn/#/policy/dynamic/index",
        "start_date": start_date,
        "end_date": end_date,
        "module_results": module_results,
        "count": len(all_items),
        "items": all_items,
    }


# ============================================================
# 增强版易车新闻采集 — 使用 Playwright 绕过验证码
# ============================================================

async def _collect_yiche_playwright(
    start_date: datetime.date,
    end_date: datetime.date,
    timeout: int,
    max_scroll: int = 3,
) -> tuple[list[dict], str, int]:
    """使用 Playwright 绕过易车验证码，采集新闻列表"""
    items = []
    source_url = "https://news.yiche.com/xinche/"
    content = ""
    content_length = 0

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            page = await context.new_page()
            await page.goto(source_url, wait_until="networkidle", timeout=timeout)
            await page.wait_for_timeout(3000)

            # 滚动加载更多
            for _ in range(max_scroll):
                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(1500)

            # 提取页面中所有新闻链接
            links = await page.eval_on_selector_all(
                "a[href*='news.yiche.com']",
                "els => els.map(el => ({href: el.href, text: el.textContent.trim()}))"
            )

            seen_urls = set()
            for link in links:
                title = clean_text(link.get("text", ""))
                url = link.get("href", "")
                if not title or not url or url in seen_urls:
                    continue
                if len(title) < 8:
                    continue
                seen_urls.add(url)

                # 提取日期
                item_date = explicit_range_date(title, start_date, end_date)
                if not item_date:
                    item_date = parse_date_from_text(title, default_year=start_date.year)
                if not item_date or not date_in_range(item_date, start_date, end_date):
                    continue

                is_car = is_new_car_post(title)
                category = "产品信息" if is_car else infer_category(title)
                confidence = "high" if is_car else "medium"

                items.append({
                    "title": title[:120],
                    "summary": title,
                    "content": title,
                    "url": url,
                    "source": "易车新车",
                    "source_type": "yiche_news_direct",
                    "date": item_date,
                    "category": category,
                    "confidence": confidence,
                    "date_confidence": "high",
                })

            body_text = await page.locator("body").inner_text()
            content = body_text
            content_length = len(body_text)
            await context.close()
            await browser.close()

    except Exception as e:
        return items, f"Error: {e}", 0

    return items, content, content_length


@mcp.tool()
def collect_yiche_news_playwright(
    start_date: str,
    end_date: str = None,
    timeout: int = 30000,
    max_scroll: int = 3,
    retries: int = 1,
) -> dict:
    """
    [增强版] 使用 Playwright 绕过易车验证码采集新车新闻。
    新闻来源于 https://news.yiche.com/xinche/
    """
    end_date = end_date or start_date
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)

    all_items = []
    last_error = None
    for attempt in range(retries + 1):
        items, content, content_length = run_coro_sync(
            _collect_yiche_playwright(start, end, timeout, max_scroll)
        )
        if items:
            all_items = items
            break
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))

    deduped = dedupe_items(all_items)
    filtered = filter_items_by_date(deduped, start_date, end_date, strict=True)

    return {
        "ok": bool(filtered),
        "source": "易车新车(Playwright)",
        "source_url": "https://news.yiche.com/xinche/",
        "start_date": start_date,
        "end_date": end_date,
        "count": len(filtered),
        "items": filtered,
        "error_type": classify_error(last_error) if last_error else None,
        "message": last_error or "",
    }


# ============================================================
# 政府新闻链接补链 — 基于 source 字段匹配官网域名
# ============================================================

@mcp.tool()
def resolve_government_news_links(
    items: list[dict],
    search_if_needed: bool = False,
) -> dict:
    """
    专门为 autoinfo.org.cn 采集的政府/政策新闻补链。
    
    策略：
    1. 从 source 字段匹配政府机构名 → 已知官网域名
    2. 构造 site:xxx.gov.cn 搜索
    3. 如果搜索不可用，返回官网首页 + 搜索建议
    
    返回 enriched items，每条含 url 和 link_source 字段。
    """
    enriched = []
    gov_count = 0
    link_found = 0
    search_attempts = 0

    for item in items:
        title = item.get("title", "") or ""
        source = item.get("source", "") or ""
        category = item.get("category", "") or infer_category(title, item.get("summary", ""))

        gov_domain = get_gov_domain(source)

        if gov_domain:
            gov_count += 1
            # 政府类 → 尝试 site 搜索
            if search_if_needed:
                clean_t = re.sub(r'[《》（）【】""「」\'，。；：]', '', title)[:40]
                query = f"site:{gov_domain} {clean_t}"
                sr = web_search(query, limit=5, retries=1)
                search_attempts += 1
                if sr.get("ok"):
                    for r in sr.get("results", []):
                        url = r.get("url", "")
                        if gov_domain in url and len(url) > 30:
                            item["url"] = url
                            item["link_source"] = f"官网({gov_domain})精确匹配"
                            link_found += 1
                            break
                    else:
                        # site 搜索未命中
                        item["url"] = f"https://www.{gov_domain}"
                        item["link_source"] = f"官网({gov_domain})首页"
                else:
                    item["url"] = f"https://www.{gov_domain}"
                    item["link_source"] = f"官网({gov_domain})首页"

            else:
                # 不搜索时直接返回官网首页
                item["url"] = f"https://www.{gov_domain}"
                item["link_source"] = f"官网({gov_domain})"

        elif category in {"政策新闻", "宏观新闻", "宏观类", "政策类"}:
            # 政策类但 source 未匹配到政府域名 → 搜索
            gov_count += 1
            if search_if_needed:
                clean_t = re.sub(r'[《》（）【】""「」\'，。；：]', '', title)[:40]
                query = f"{clean_t} {source}"
                sr = web_search(query, limit=5, retries=1)
                search_attempts += 1
                if sr.get("ok"):
                    for r in sr.get("results", []):
                        url = r.get("url", "")
                        domain = extract_domain(url)
                        if ".gov.cn" in domain or len(url) > 30:
                            item["url"] = url
                            item["link_source"] = "搜索匹配"
                            link_found += 1
                            break

        enriched.append(item)

    return {
        "ok": True,
        "total": len(enriched),
        "government_items": gov_count,
        "link_found": link_found,
        "search_attempts": search_attempts,
        "items": enriched,
    }


# ============================================================
# 全信源统一采集
# ============================================================

# 聚合/拼盘类 URL 排除列表
AGGREGATOR_PATTERNS = [
    "msn.cn", "msn.com",
    "baijiahao.baidu.com",
    "toutiao.com",
    "hao123.com",
    "k.sina.com.cn",
    "qianzhan.com",
    "baidu.com/s",       # 百度搜索结果页
    "so.com/s",          # 360搜索页
    "sogou.com/web",     # 搜狗搜索页
]


def is_aggregator(url: str) -> bool:
    """检查是否是聚合/拼盘类链接"""
    url_l = url.lower()
    for p in AGGREGATOR_PATTERNS:
        if p in url_l:
            return True
    return False


def is_article_link(url: str) -> bool:
    """
    判断 URL 是否是具体文章链接（而非首页/聚合页/搜索页）。
    接受：baidu.com/link 跳转链、具体文章 URL、带路径的 URL
    """
    if not url or len(url) < 15:
        return False
    if is_aggregator(url):
        return False
    url_l = url.lower()
    # Baidu 跳转链 → 会重定向到具体文章
    if "baidu.com/link" in url_l:
        return True
    # 有具体路径（非首页）
    path = url.split("/")[3:] if len(url.split("/")) > 3 else []
    if path and len("/".join(path)) > 10:
        return True
    return False


@mcp.tool()
def collect_all_sources(
    date: str,
    end_date: str = None,
    weibo_cookie: str = None,
    resolve_links: bool = True,
    include_yiche: bool = True,  # 使用 Playwright 绕过腾讯验证码采集（需 Render Docker 环境）
    include_autoinfo: bool = True,
    include_cls: bool = True,
    include_jiemian: bool = True,
    include_sina: bool = True,
    include_autohome: bool = True,
    include_new_car: bool = True,
    autoinfo_max_pages: int = 5,
    yiche_timeout: int = 30000,
) -> dict:
    """
    全信源统一采集 + 链接补充。
    
    采集以下所有信源并去重合并：
    - 中国汽车工业信息网政策动态（API直调，无验证码）
    - 易车新车新闻（Playwright绕过验证码）
    - 财联社汽车早报
    - 界面新闻汽车早报
    - 新浪汽车7x24快讯
    - 汽车之家上市新车
    - 新车上市聚合（易车+汽车之家）
    
    参数:
        date: 采集日期 YYYY-MM-DD
        end_date: 结束日期（不传则只采单日）
        resolve_links: 是否自动补充原始链接
        weibo_cookie: 微博 Cookie（可选，用于微博采集）
        include_*: 开关各个信源
        autoinfo_max_pages: autoinfo 每个 API 的最大翻页数
        yiche_timeout: 易车 Playwright 超时(ms)
    """
    end = end_date or date

    sources_config = []
    if include_autoinfo:
        sources_config.append({"name": "中国汽车工业信息网(API直调)", "type": "autoinfo_direct"})
    if include_cls:
        sources_config.append({"name": "财联社汽车早报", "type": "cls_auto_morning", "timeout": 20})
    if include_jiemian:
        sources_config.append({"name": "界面新闻汽车早报", "type": "jiemian_auto_morning", "timeout": 20, "max_candidates": 30})
    if include_sina:
        sources_config.append({"name": "新浪汽车7x24快讯", "type": "sina_auto_7x24", "pages": 12, "limit": 20})
    if include_autohome:
        sources_config.append({"name": "汽车之家上市新车", "type": "autohome_newbrand", "timeout": 20, "link_limit": 80, "fetch_details": True})
    if include_new_car:
        sources_config.append({"name": "新车上市聚合", "type": "new_car_launches", "timeout": 20, "limit": 20})
    if include_yiche:
        sources_config.append({"name": "易车新车(Playwright)", "type": "yiche_playwright"})

    source_results = []
    all_items = []

    # ---- 信源1-6: 复用 batch_collect_sources ----
    if sources_config:
        batch = batch_collect_sources(
            sources=[
                {k: v for k, v in s.items() if k != "type"}
                | {"type": s["type"] if s["type"] not in ("autoinfo_direct", "yiche_playwright") else "search"}
                for s in sources_config
                if s["type"] not in ("autoinfo_direct", "yiche_playwright")
            ],
            start_date=date,
            end_date=end,
            default_dynamic=False,
            dedupe=True,
            strict_date=True,
            resolve_original_links=resolve_links,
        )
        if batch.get("ok"):
            all_items.extend(batch.get("items", []))
        for sr in batch.get("sources", []):
            source_results.append(sr)

    # ---- 信源A: autoinfo 直调 API ----
    if include_autoinfo:
        autoinfo_result = collect_autoinfo_all(
            start_date=date,
            end_date=end,
            max_pages=autoinfo_max_pages,
        )
        source_results.append({
            "name": "中国汽车工业信息网(API直调)",
            "ok": autoinfo_result.get("ok", False),
            "count": autoinfo_result.get("count", 0),
            "type": "autoinfo_direct",
            "modules": autoinfo_result.get("module_results", []),
        })
        if autoinfo_result.get("items"):
            all_items.extend(autoinfo_result["items"])

    # ---- 信源B: 易车 Playwright ----
    if include_yiche:
        yiche_result = collect_yiche_news_playwright(
            start_date=date,
            end_date=end,
            timeout=yiche_timeout,
            max_scroll=3,
            retries=1,
        )
        source_results.append({
            "name": "易车新车(Playwright)",
            "ok": yiche_result.get("ok", False),
            "count": yiche_result.get("count", 0),
            "type": "yiche_playwright",
        })
        if yiche_result.get("items"):
            all_items.extend(yiche_result["items"])

    # ---- 去重 + 日期过滤 ----
    all_items = dedupe_items(all_items)
    all_items = filter_items_by_date(all_items, date, end, strict=True)

    # ---- 链接补充（全面修复！） ----
    link_quality = {"missing_original_link_count": 0, "original_link_count": 0}

    if resolve_links:
        # 对所有 items 做逐条链接补充
        enriched_items = []
        for item in all_items:
            title = item.get("title", "") or ""
            source = item.get("source", "") or ""
            source_type = item.get("source_type", "") or ""
            current_url = item.get("url", "") or ""
            summary = item.get("summary", "") or ""

            # 跳过已有好链接的（官网、垂媒、百度跳转链都算好链接）
            if current_url and (not is_aggregator(current_url) or "baidu.com/link" in current_url):
                if any(d in current_url for d in [".gov.cn", "autohome.com.cn", "yiche.com", "dongchedi.com", "baidu.com/link"]):
                    enriched_items.append(item)
                    continue

            best_url = current_url
            best_source = ""

            # 策略1: autoinfo 政府新闻 → 官网域名映射
            gov_domain = get_gov_domain(source)
            if gov_domain:
                best_url = f"https://www.{gov_domain}"
                best_source = f"官网({gov_domain})"
                # 尝试搜索精确链接
                if 'web_search' in dir() or True:  # web_search 始终可用
                    clean_t = re.sub(r'[《》（）【】""「」\'，。；：]', '', title)[:40]
                    sr = web_search(f"site:{gov_domain} {clean_t}", limit=5, retries=1)
                    if sr.get("ok"):
                        for r in sr.get("results", []):
                            url = r.get("url", "")
                            # 接受：直接命中官网域名 或 Baidu跳转链（会重定向到官网文章）
                            if (gov_domain in url and len(url) > 30) or "baidu.com/link" in url:
                                best_url = url
                                best_source = f"官网({gov_domain})精确"
                                break

            # 策略2: 界面新闻子条目 → 搜索独立链接
            elif source_type in ("jiemian_auto_morning",) or "界面新闻" in source:
                clean_t = title[:40]
                # 多轮搜索策略
                found = False
                for search_q in [
                    f"{clean_t} 界面新闻",
                    f"site:jiemian.com {clean_t}",
                    f"{clean_t}",
                ]:
                    sr = web_search(search_q, limit=5, retries=1)
                    if sr.get("ok"):
                        for r in sr.get("results", []):
                            url = r.get("url", "")
                            if ("jiemian.com/article" in url and url != current_url) or "baidu.com/link" in url:
                                # 接受直接匹配 或 baidu跳转链（点击后到独立文章）
                                if "baidu.com/link" in url:
                                    # 检查摘要是否匹配
                                    snippet = r.get("snippet","") or ""
                                    if len(snippet) > 20 and any(kw in (snippet+title) for kw in title[:10].split()):
                                        pass  # baidu redirect is acceptable
                                    else:
                                        continue
                                best_url = url
                                best_source = "界面新闻独立文章"
                                found = True
                                break
                    if found:
                        break
                if not found:
                    best_url = current_url
                    best_source = "界面新闻早报(摘要页)"

            # 策略3: 财联社子条目 → 搜索独立链接
            elif source_type in ("cls_auto_morning",) or "财联社" in source:
                clean_t = title[:40]
                found = False
                for search_q in [
                    f"{clean_t} 财联社",
                    f"site:cls.cn {clean_t}",
                    f"site:clsi.com.cn {clean_t}",
                    f"{clean_t}",
                ]:
                    sr = web_search(search_q, limit=5, retries=1)
                    if sr.get("ok"):
                        for r in sr.get("results", []):
                            url = r.get("url", "")
                            if ("cls.cn" in url or "clsi.com.cn" in url or "baidu.com/link" in url):
                                best_url = url
                                best_source = "财联社独立文章"
                                found = True
                                break
                    if found:
                        break
                if not found:
                    best_url = current_url
                    best_source = "财联社早报(摘要页)"

            # 策略4: 易车 → 保留采集到的链接或用垂媒搜索
            elif "易车" in source or source_type == "yiche_news_direct":
                if current_url and "yiche.com" in current_url:
                    best_url = current_url
                    best_source = "易车新车"
                else:
                    clean_t = title[:40]
                    sr = web_search(f"site:yiche.com {clean_t}", limit=5, retries=1)
                    if sr.get("ok"):
                        for r in sr.get("results", []):
                            url = r.get("url", "")
                            if "yiche.com" in url or "bitauto.com" in url:
                                best_url = url
                                best_source = "易车搜索"
                                break
                    if not best_source:
                        # 兜底：搜索汽车之家
                        sr2 = web_search(f"site:autohome.com.cn {clean_t}", limit=5, retries=1)
                        if sr2.get("ok"):
                            for r in sr2.get("results", []):
                                url = r.get("url", "")
                                if "autohome.com.cn" in url:
                                    best_url = url
                                    best_source = "汽车之家搜索"
                                    break

            # 策略5: 新浪7x24 → 保留原链接或搜索新浪
            elif "新浪" in source or source_type == "sina_auto_7x24":
                if current_url and "sina.com.cn" in current_url and "/7x24" not in current_url:
                    best_url = current_url
                    best_source = "新浪汽车"
                else:
                    clean_t = title[:40]
                    sr = web_search(f"site:sina.com.cn {clean_t}", limit=5, retries=1)
                    if sr.get("ok"):
                        for r in sr.get("results", []):
                            url = r.get("url", "")
                            if "sina" in url and len(url) > 35:
                                best_url = url
                                best_source = "新浪搜索"
                                break
                    if not best_source:
                        best_url = current_url
                        best_source = "新浪7x24(快讯页)"

            # 策略6: 汽车之家
            elif "汽车之家" in source or source_type == "autohome_newbrand":
                if "autohome.com.cn" in current_url:
                    best_url = current_url
                    best_source = "汽车之家"

            # 策略7: 通用搜索兜底
            if not best_source and current_url:
                if is_aggregator(current_url) or "baidu.com/s" in current_url:
                    clean_t = title[:40]
                    sr = web_search(clean_t, limit=5, retries=1)
                    if sr.get("ok"):
                        for r in sr.get("results", []):
                            url = r.get("url", "")
                            if not is_aggregator(url) and len(url) > 30:
                                best_url = url
                                best_source = "通用搜索匹配"
                                break

            item["url"] = best_url
            item["link_source"] = best_source or "未补链"
            enriched_items.append(item)

        all_items = enriched_items

        # 统计链接质量（接受 baidu.com/link 跳转链、直接域名、具体文章URL）
        good_links = sum(1 for i in all_items if i.get("url") and (
            not is_aggregator(i["url"]) or "baidu.com/link" in i["url"]
        ))
        total = len(all_items)
        link_quality = {
            "missing_original_link_count": total - good_links,
            "original_link_count": good_links,
        }

    table_rows = items_to_table_rows(all_items, default_date=date, resolve_links=True)

    success_sources = sum(1 for s in source_results if s.get("ok"))
    total_sources = len(source_results)

    return {
        "ok": True,
        "date": date,
        "end_date": end,
        "sources": source_results,
        "source_count": total_sources,
        "source_success": success_sources,
        "count": len(all_items),
        "items": all_items,
        "table_columns": TABLE_COLUMNS,
        "table_rows": table_rows,
        "table_workbook": build_table_workbook_payload(
            table_rows, sheet_name=f"{date[:7]}行业快讯" if date else "行业快讯"
        ),
        "link_quality": {
            "resolve_original_links": resolve_links,
            **link_quality,
            "rule": (
                "链接优先级：政府官网 > 微信公众号/微博 > 垂媒(易车/汽车之家/懂车帝) > "
                "新浪财经。排除新闻拼盘类信源。"
            ),
        },
        "quality": {
            "coverage_score": round(
                (success_sources / max(total_sources, 1)) * 0.5
                + min(len(all_items), 40) / 40 * 0.5, 3
            ),
            "source_success_rate": round(success_sources / max(total_sources, 1), 3),
        },
    }


# ============================================================
# 在本文件中注册新工具的等价方式（当此文件被 import 时）
# ============================================================

if __name__ == "__main__":
    # 独立运行时打印可用工具列表
    print("=" * 60)
    print("MCP 增强模块已加载")
    print("新增 MCP 工具:")
    print("  1. collect_autoinfo_all         — autoinfo API直调（4个模块）")
    print("  2. collect_yiche_news_playwright  — 易车 Playwright 绕过验证码")
    print("  3. collect_all_sources          — 全信源统一采集+链接补充")
    print("  4. resolve_government_news_links — 政府新闻链接补链")
    print("=" * 60)
