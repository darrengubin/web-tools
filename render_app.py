"""
Render 专用 MCP 入口 (render_app.py)
======================================
适配 EAI 的 streamable-http 传输方式。

EAI 配置:
  {
    "url": "https://web-tools-khac.onrender.com/mcp",
    "headers": {},
    "transport": "streamable-http"
  }

核心变更：
  1. 使用 mcp.streamable_http_app() 创建 ASGI app
  2. Streamable HTTP 端点默认在 /mcp（与 EAI 配置匹配）
  3. 添加 / 首页和 /health 健康检查
  4. 由 uvicorn 统一托管
"""

import os
import sys
import json
import datetime
import re

# ============================================================
# 第1步：读取并合并主模块 + 增强模块
# ============================================================
here = os.path.dirname(os.path.abspath(__file__))
main_path = os.path.join(here, "mcp_web_tools.py")
addendum_path = os.path.join(here, "mcp_addendum.py")

with open(main_path, "r", encoding="utf-8") as f:
    source = f.read()

# 替换最后的 if __name__ 块为 Streamable HTTP 启动方式
render_entry = """
# ===== Render Streamable HTTP 启动 =====
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse

# 创建 Streamable HTTP ASGI app
# 端点默认在 /mcp，与 EAI 配置完全匹配
http_app = mcp.streamable_http_app()

# 包装为完整的 Starlette 应用
app = Starlette(routes=[
    Route("/", endpoint=lambda r: JSONResponse({
        "name": "MCP 全信源采集服务",
        "version": "2.0",
        "status": "running",
        "transport": "streamable-http",
        "mcp_endpoint": "/mcp",
        "health_endpoint": "/health",
        "available_tools": [
            "collect_all_sources(date, resolve_links=True)",
            "collect_autoinfo_all(start_date, end_date)",
            "collect_yiche_news_playwright(start_date, end_date)",
            "web_search(keyword, limit=10)",
            "web_fetch(url, timeout=15)",
            "web_fetch_dynamic(url, timeout=30000)",
            "resolve_news_original_links(items, default_date)",
            "resolve_government_news_links(items, search_if_needed)",
            "batch_collect_sources(sources, start_date, end_date)",
            "collect_auto_5_sources(date)",
            "collect_cls_auto_morning(start_date, end_date)",
            "collect_jiemian_auto_morning(start_date, end_date)",
            "collect_sina_auto_7x24(start_date, end_date)",
            "collect_autohome_newbrand(start_date, end_date)",
            "collect_yiche_xinche_news(start_date, end_date)",
            "collect_new_car_launches(start_date, end_date)",
        ],
    })),
    Route("/health", endpoint=lambda r: JSONResponse({
        "status": "healthy",
        "service": "information_fetch",
        "transport": "streamable-http",
        "mcp_endpoint": "/mcp",
        "timestamp": datetime.datetime.now().isoformat(),
    })),
    # Streamable HTTP MCP 端点
    Mount("/", app=http_app),
])

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting MCP on Render: host=0.0.0.0 port={port}")
    print(f"  MCP endpoint:    /mcp  (Streamable HTTP)")
    print(f"  Health check:    /health")
    print(f"  EAI config:      {{\"url\": \"https://host/mcp\", \"transport\": \"streamable-http\"}}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
"""

# 替换原有的 if __name__ 块
source = re.sub(
    r'if __name__ == "__main__":.*?(?:\n\s*\n|\Z)',
    render_entry,
    source,
    flags=re.DOTALL,
)

# 加载增强模块
if os.path.exists(addendum_path):
    with open(addendum_path, "r", encoding="utf-8") as f:
        addendum = f.read()
    addendum = re.sub(r'if __name__ == "__main__":.*', "", addendum, flags=re.DOTALL)
    source += "\n\n\n" + addendum

# 执行
exec_globals = {"__name__": "__main__", "__file__": main_path, "__builtins__": __builtins__}
exec(compile(source, main_path, "exec"), exec_globals)
