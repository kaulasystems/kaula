# Kaula workspace commands — kept in sync with CLAUDE.md §Commands.

PKG ?=
SRC_DIRS := $(wildcard packages/*/src)

.PHONY: install test lint format typecheck seam-check demo-healing build check-wheel publish-test publish

install:
	uv sync

test:
ifeq ($(PKG),)
	uv run pytest packages
else
	uv run pytest packages/$(PKG)/tests
endif

lint:
	uv run ruff check packages examples scripts
	uv run black --check packages examples scripts

format:
	uv run ruff check --fix packages examples scripts
	uv run black packages examples scripts

typecheck:
	uv run mypy $(SRC_DIRS)

seam-check:
	uv run python scripts/check_seam.py

# the canonical demo: break a tool, watch it heal, inspect the audit trail
demo-healing:
	uv run python examples/demo_healing.py

build:
ifeq ($(PKG),)
	$(error usage: make build PKG=kaula-core)
endif
	uv build --package $(PKG) --out-dir dist/

check-wheel:
ifeq ($(PKG),)
	$(error usage: make check-wheel PKG=kaula-core)
endif
	uv run python scripts/check_wheel.py dist/$(subst -,_,$(PKG))-*.whl

# Releases go through CI (Trusted Publishing) only — see CLAUDE.md publishing rules.
publish-test publish:
	@echo "ERROR: never publish manually; releases run in CI via Trusted Publishing." >&2
	@exit 1
