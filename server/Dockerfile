# The control plane's image: dependencies from uv.lock, then the package, run as a user
# without privileges. The command migrates, bootstraps from the environment when no
# organization exists yet, and serves.
FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev

FROM python:3.14-slim
# Debian already has a "floppy" group (the device), hence another name.
RUN useradd --system --uid 10001 --user-group --home-dir /app controlplane
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER controlplane
EXPOSE 8000
CMD ["sh", "-c", "floppy-server migrate && floppy-server bootstrap --from-env --if-needed && floppy-server serve --host 0.0.0.0 --port 8000"]
