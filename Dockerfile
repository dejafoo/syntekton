# Product Factory remote mock sandbox (PM3.0).
# Force-mock + remote mode; no OpenRouter / GPU required.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PRODUCT_FACTORY_ROOT=/app \
    PATH="/app/.venv/bin:$PATH"

# SD5: install from the committed lock. Prefer digest-pinning the base image
# for release builds; record the resolved digest in provenance notes when
# publishing (see docs/evidence/sustainable-development/sd5/).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY config ./config
COPY profiles ./profiles
COPY skills ./skills
COPY tests/fixtures/sample_api ./fixtures/sample_api
COPY examples/remote/docker-entrypoint.sh /docker-entrypoint.sh

RUN chmod +x /docker-entrypoint.sh \
    && uv sync --frozen --extra observability --no-dev --no-editable

EXPOSE 8765

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["product-factory", "observe", "serve", "--host", "0.0.0.0", "--port", "8765"]
