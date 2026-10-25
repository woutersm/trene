FROM ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --extra mcp --no-install-project
COPY src ./src
RUN uv sync --locked --extra mcp
ENV PATH="/app/.venv/bin:$PATH" TRENE_HOME=/data TZ=Europe/Amsterdam
VOLUME /data
EXPOSE 8000
CMD ["trene-mcp", "--http"]
