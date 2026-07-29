FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    HOST=0.0.0.0 \
    MCP_TRANSPORT=sse

COPY requirements.txt .

RUN pip install --upgrade pip setuptools wheel && \
    pip uninstall -y mcp || true && \
    pip install --no-cache-dir -r requirements.txt && \
    python -c "from mcp.server.fastmcp import FastMCP; print('FastMCP import OK')"

COPY . .

EXPOSE 8000

CMD ["python", "-u", "mcp_web_tools.py"]
