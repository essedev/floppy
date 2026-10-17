SCRIPTS := macos/bin/floppy macos/install.sh macos/vm/guest/guest-install.sh \
	macos/vm/guest/floppy-mount scripts/package.sh spike/setup.sh spike/bench.sh spike/data-bench.sh spike/meta-bench.sh spike/meta-access.sh \
	spike/juicefs-replica/build.sh spike/juicefs-replica/test.sh \
	spike/drive/entrypoint.sh

.DEFAULT_GOAL := help

help: ## Show the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

lint: ## bash -n, shellcheck and the prose language check
	@for f in $(SCRIPTS); do bash -n "$$f" || exit 1; done
	shellcheck $(SCRIPTS)
	python3 scripts/check-prose.py

check: lint ## The full quality pass (there are no tests yet)

.PHONY: help lint check
