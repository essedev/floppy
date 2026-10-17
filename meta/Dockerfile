# The meta service's image: Valkey's official image (Debian trixie), with a Python managed
# by uv beside it, and the service, run as Valkey's own unprivileged user. Every instance is a
# child process of the service; the data and the configuration of each live under /data.
#
#   docker build -t floppy-meta meta/
#   docker build --target test -t floppy-meta-test meta/ && docker run --rm floppy-meta-test
FROM valkey/valkey:8.1.4 AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv python install 3.14 && uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev

# The tests need valkey-server, so they run in the image: `docker run --rm floppy-meta-test`.
FROM build AS test
RUN uv sync --locked --no-install-project
COPY tests ./tests
RUN uv sync --locked && chown -R valkey:valkey /app
USER valkey
ENV PATH="/app/.venv/bin:$PATH"
CMD ["pytest", "-q"]

FROM valkey/valkey:8.1.4
COPY --from=build /opt/python /opt/python
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
RUN install -d -o valkey -g valkey -m 0700 /data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 FLOPPY_META_DATA_DIR=/data
WORKDIR /app
USER valkey
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
# The image's entrypoint (docker-entrypoint.sh) is Valkey's; the service starts its own.
ENTRYPOINT []
CMD ["floppy-meta"]
