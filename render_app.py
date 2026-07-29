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
# ===== Render Streamable HTTP 启动（正确方式） =====
# streamable_http_app() 已自带 lifespan（self.session_manager.run()）
# 不需要再包一层 Starlette！直接用它返回的 app，添加路由即可。
import os, json, uvicorn
import nest_asyncio
from starlette.routing import Route
from starlette.responses import JSONResponse

nest_asyncio.apply()

# 设置 streamable_http 路径为 /mcp（匹配 EAI 配置）
mcp.settings.streamable_http_path = "/mcp"

# streamable_http_app() 返回的 Starlette app 自带 lifespan
# lifespan 会调用 self.session_manager.run() 初始化 TaskGroup
app = mcp.streamable_http_app()

# 添加自定义路由（健康检查 + 首页）
app.routes.append(Route("/health", endpoint=lambda r: JSONResponse({
    "status": "healthy", "mcp_endpoint": "/mcp", "transport": "streamable-http",
})))
app.routes.append(Route("/", endpoint=lambda r: JSONResponse({
    "name": "MCP 全信源采集服务", "status": "running", "mcp_endpoint": "/mcp",
})))

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    eai_cfg = json.dumps({"url": "https://host/mcp", "transport": "streamable-http"})
    print(f"Starting MCP on Render: host=0.0.0.0 port={port}")
    print(f"  MCP endpoint: /mcp (Streamable HTTP)")
    print(f"  EAI config:   {eai_cfg}")
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
