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
# ===== Render HTTP 启动（唯一正确方式）=====
import uvicorn
import nest_asyncio
nest_asyncio.apply()

# 使用 mcp.run() 而不是 streamable_http_app()
# mcp.run() 会正确初始化 session_manager 的 TaskGroup
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting MCP on Render: host=0.0.0.0 port={port} endpoint=/mcp")
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
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
