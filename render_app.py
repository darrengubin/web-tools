"""
Render 专用 MCP 入口 (render_app.py)
======================================
适配 EAI 的 streamable-http 传输方式。
EAI 配置: {"url": "https://host/mcp", "transport": "streamable-http"}

核心设计：先加载所有模块注册全部工具，最后再启动 uvicorn。
"""

import os
import sys
import json
import re

here = os.path.dirname(os.path.abspath(__file__))
main_path = os.path.join(here, "mcp_web_tools.py")
addendum_path = os.path.join(here, "mcp_addendum.py")

# ============================================================
# 第1步：读取主模块
# ============================================================
with open(main_path, "r", encoding="utf-8") as f:
    source = f.read()

# ============================================================
# 第2步：替换 if __name__ 为 setup 代码（不含 uvicorn.run）
# ============================================================
setup_code = """
# ===== Streamable HTTP 配置（工具注册后启动） =====
import os, json, uvicorn
import nest_asyncio
from starlette.routing import Route
from starlette.responses import JSONResponse

nest_asyncio.apply()
mcp.settings.streamable_http_path = "/mcp"

# 创建 ASGI app（streamable_http_app() 自带 lifespan）
_RENDER_APP = mcp.streamable_http_app()

# 添加健康检查和首页路由
_RENDER_APP.routes.append(Route("/health", endpoint=lambda r: JSONResponse({
    "status": "healthy", "mcp_endpoint": "/mcp", "transport": "streamable-http",
})))
_RENDER_APP.routes.append(Route("/", endpoint=lambda r: JSONResponse({
    "name": "MCP 全信源采集服务", "status": "running", "mcp_endpoint": "/mcp",
})))
"""

source = re.sub(
    r'if __name__ == "__main__":.*?(?:\n\s*\n|\Z)',
    setup_code,
    source,
    flags=re.DOTALL,
)

# ============================================================
# 第3步：加载增强模块（注册 collect_all_sources 工具）
# ============================================================
if os.path.exists(addendum_path):
    with open(addendum_path, "r", encoding="utf-8") as f:
        addendum = f.read()
    addendum = re.sub(r'if __name__ == "__main__":.*', "", addendum, flags=re.DOTALL)
    source += "\n\n\n" + addendum

# ============================================================
# 第4步：追加启动代码（所有工具注册完毕后）
# ============================================================
startup = """
# ===== 启动 uvicorn（所有工具已注册） =====
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    eai_cfg = json.dumps({"url": "https://host/mcp", "transport": "streamable-http"})
    print(f"Starting MCP on Render: host=0.0.0.0 port={port}")
    print(f"  MCP endpoint: /mcp (Streamable HTTP)")
    print(f"  EAI config:   {eai_cfg}")
    uvicorn.run(_RENDER_APP, host="0.0.0.0", port=port, log_level="info")
"""

source += "\n\n\n" + startup

# ============================================================
# 第5步：执行
# ============================================================
exec_globals = {"__name__": "__main__", "__file__": main_path, "__builtins__": __builtins__}
exec(compile(source, main_path, "exec"), exec_globals)
