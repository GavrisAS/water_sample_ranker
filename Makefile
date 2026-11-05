UV ?= uv
BUILD_VERSION ?= 0.1.1
DEMO_DIR ?= artifacts/private/demo-make
AUDIT_ARGS ?=
ARGS ?=

.PHONY: help sync lint format format-check typecheck test check run cli version \
        demo build build-windows privacy public-check audit clean

help:
	@echo "make sync / check / run / cli ARGS='--help' / version"
	@echo "make demo DEMO_DIR=artifacts/private/new-demo"
	@echo "make build / build-windows BUILD_VERSION=0.1.1"
	@echo "make privacy / public-check: clean public projection; audit: real project root"
	@echo "make clean: caches only; inputs, outputs and private artifacts are preserved"

sync:
	$(UV) sync --locked

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format .

format-check:
	$(UV) run ruff format --check .

typecheck:
	$(UV) run ty check

test:
	$(UV) run pytest

check:
	$(MAKE) sync
	$(MAKE) lint
	$(MAKE) format-check
	$(MAKE) typecheck
	$(MAKE) test

run:
	$(UV) run water-sample-ranker-gui

cli:
	$(UV) run water-sample-ranker $(or $(ARGS),--help)

version:
	$(UV) run water-sample-ranker --version

demo:
	$(UV) run python -m scripts.make_synthetic_demo --output-dir "$(DEMO_DIR)"

build:
	$(UV) run python -m scripts.build_frozen --version "$(BUILD_VERSION)"

build-windows:
	pwsh -NoProfile -File scripts/build_windows.ps1 -BuildVersion "$(BUILD_VERSION)"

privacy:
	$(UV) run python -m scripts.public_gate

public-check:
	$(UV) run python -m scripts.run_checks --public

audit:
	$(UV) run python -m scripts.build_handoff --mode share-safe --include-template-binary $(AUDIT_ARGS)

clean:
	$(UV) run python -c "from pathlib import Path; import shutil; [shutil.rmtree(Path(p), ignore_errors=True) for p in ('.pytest_cache', '.ruff_cache', '.ty_cache')]"
