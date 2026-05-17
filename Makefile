PYTHON ?= python3
PYTHONPATH := $(CURDIR):$(CURDIR)/memory
export PYTHONPATH

.PHONY: help test smoke audit lint validate clean install-dev

help:
	@echo "UCW developer commands:"
	@echo "  make test        # run pytest"
	@echo "  make smoke       # run the end-to-end demo (scripts/smoke.sh)"
	@echo "  make audit       # security audit of this repo"
	@echo "  make lint        # shellcheck install.sh + JSON manifest validation"
	@echo "  make validate    # everything CI runs"
	@echo "  make clean       # remove __pycache__ etc."
	@echo "  make install-dev # editable install of the memory package"

test:
	@if $(PYTHON) -c "import pytest" 2>/dev/null; then \
	  $(PYTHON) -m pytest -q tests; \
	else \
	  pytest -q tests; \
	fi

smoke:
	./scripts/smoke.sh

audit:
	$(PYTHON) bin/ucw-audit.py --repo .

lint:
	@if command -v shellcheck >/dev/null 2>&1; then shellcheck install.sh scripts/smoke.sh; \
	  else echo "(shellcheck not installed — skipping)"; fi
	@for f in .claude-plugin/*.json settings/*.json mcp/ucw-memory.json mcp/obsidian.json.example mcp/notion.json.example; do \
	  $(PYTHON) -c "import json; json.load(open('$$f'))" && echo "ok $$f"; \
	done

validate: test lint audit
	@echo "✓ all green"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true

install-dev:
	$(PYTHON) -m pip install -e ./memory
	@echo "ucw-memory installed in editable mode. Try: ucw-memory stats"
