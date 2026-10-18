SCRIPTS := macos/bin/floppy macos/install.sh macos/vm/guest/guest-install.sh \
	macos/vm/guest/floppy-mount macos/vm/guest/floppy-disks macos/tests/engine.sh scripts/package.sh scripts/build-engine.sh scripts/build-app.sh \
	scripts/release-app.sh scripts/release-notes.sh scripts/setup-signing.sh packaging/homebrew/render.sh spike/setup.sh spike/bench.sh spike/data-bench.sh spike/meta-bench.sh spike/ovh-bench.sh spike/meta-access.sh spike/meta-tls.sh \
	spike/juicefs-replica/build.sh spike/juicefs-replica/test.sh \
	spike/drive/entrypoint.sh spike/drive/perdisk-entrypoint.sh app/scripts/render-marks.sh server/docker/db-init.sh deploy/local/stack.sh

.DEFAULT_GOAL := help

help: ## Show the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

lint: ## bash -n, shellcheck and the prose language check
	@for f in $(SCRIPTS); do bash -n "$$f" || exit 1; done
	shellcheck $(SCRIPTS)
	python3 scripts/check-prose.py

engine-test: ## The floppy command without a VM: engine lock, disks clear (fake limactl)
	macos/tests/engine.sh

app-check: ## The menu bar app: typecheck and build, frontend and Rust tests, clippy
	cd app && pnpm install --frozen-lockfile && pnpm build && pnpm test
	cd app && cargo clippy --all-targets -- -D warnings && cargo test

app: ## Floppy.app with the engine inside and the cask's DMG, in dist/app (needs Docker)
	scripts/build-app.sh

web-check: ## The admin web: typecheck, lint and build
	cd web && pnpm install --frozen-lockfile && pnpm typecheck && pnpm lint && pnpm build

server-check: ## The control plane: format, lint, types and tests (needs `docker compose --profile test up -d --build db meta-test-1 meta-test-2 meta-test-3 meta-test` in server/)
	cd server && uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run --env-file .env pytest

site-check: ## The site: Astro check and build
	cd site && pnpm install --frozen-lockfile && pnpm check && pnpm build

meta-check: ## The meta service: format, lint, types, and its tests in its image
	cd meta && uv run ruff format --check . && uv run ruff check . && uv run mypy
	docker build --target test -t floppy-meta-test meta/ && docker run --rm floppy-meta-test

check: lint engine-test app-check web-check server-check meta-check site-check ## The full quality pass

.PHONY: help lint engine-test app-check app web-check server-check meta-check site-check check
