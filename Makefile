SCRIPTS := macos/bin/floppy macos/install.sh macos/vm/guest/guest-install.sh \
	macos/vm/guest/floppy-mount scripts/package.sh spike/setup.sh spike/bench.sh spike/data-bench.sh spike/meta-bench.sh spike/meta-access.sh \
	spike/juicefs-replica/build.sh spike/juicefs-replica/test.sh \
	spike/drive/entrypoint.sh app/scripts/render-marks.sh

.DEFAULT_GOAL := help

help: ## Show the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

lint: ## bash -n, shellcheck and the prose language check
	@for f in $(SCRIPTS); do bash -n "$$f" || exit 1; done
	shellcheck $(SCRIPTS)
	python3 scripts/check-prose.py

app-check: ## The menu bar app: typecheck and build, frontend and Rust tests, clippy
	cd app && pnpm install --frozen-lockfile && pnpm build && pnpm test
	cd app && cargo clippy --all-targets -- -D warnings && cargo test

web-check: ## The admin web: typecheck, lint and build
	cd web && pnpm install --frozen-lockfile && pnpm typecheck && pnpm lint && pnpm build

site-check: ## The site: Astro check and build
	cd site && pnpm install --frozen-lockfile && pnpm check && pnpm build

check: lint app-check web-check site-check ## The full quality pass

.PHONY: help lint app-check web-check site-check check
