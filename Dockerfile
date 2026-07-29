# ============================================================
# Docker 部署配置 — 完整的 MCP 爬虫服务
# 构建命令: docker build -t mcp-web-tools -f Dockerfile .
# 运行命令: docker run -p 8000:8000 \
#   -e BING_SEARCH_API_KEY=your_key \
#   mcp-web-tools
# ============================================================
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    HOST=0.0.0.0 \
    MCP_TRANSPORT=sse

# 安装系统依赖（Playwright 内置，但额外中文支持）
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-zenhei fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip setuptools wheel && \
    pip uninstall -y mcp || true && \
    pip install --no-cache-dir -r requirements.txt && \
    python -c "from mcp.server.fastmcp import FastMCP; print('FastMCP import OK')" && \
    python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"

# render_app.py 使用 mcp.sse_app() ASGI 模式运行（修复 Render 404）
COPY render_app.py mcp_web_tools.py mcp_addendum.py ./

EXPOSE 10000

# 使用 ASGI 模式，Render 自动分配 PORT 环境变量
CMD ["python", "-u", "render_app.py"]
