PYTHON ?= python3
PYTHONPATH := $(CURDIR):$(CURDIR)/memory
export PYTHONPATH

.PHONY: help test smoke audit lint validate clean install-dev

help:
	@echo "UCW developer commands:"
	@echo "  make test        # run pytest"
	@echo "  make smoke       # run the end-to-end demo (scripts/smoke.sh)"
	@echo "  make audit       # security audit of this repo"
	@echo "  make lint        # ruff + shellcheck + JSON manifest validation"
	@echo "  make format      # ruff check --fix + ruff format"
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
	@if command -v ruff >/dev/null 2>&1; then ruff check .; else echo "SKIPPED (ruff not installed)"; fi
	@if command -v shellcheck >/dev/null 2>&1; then shellcheck install.sh scripts/smoke.sh dashboard/statusline.sh; \
	  else echo "SKIPPED (shellcheck not installed)"; fi
	@for f in .claude-plugin/*.json settings/*.json mcp/ucw-memory.json mcp/obsidian.json.example mcp/notion.json.example; do \
	  $(PYTHON) -c "import json; json.load(open('$$f'))" && echo "ok $$f"; \
	done

format:
	@if command -v ruff >/dev/null 2>&1; then ruff check --fix . && ruff format .; \
	  else echo "ruff not installed — pip install ruff first"; fi

# validate must not print an unqualified success when lint gates were skipped
# for missing tools — count the skips the same way `lint` detects them and
# qualify the summary line so "green" is never mistaken for "everything ran".
validate: test lint audit
	@skipped=0; \
	command -v ruff >/dev/null 2>&1 || skipped=$$((skipped + 1)); \
	command -v shellcheck >/dev/null 2>&1 || skipped=$$((skipped + 1)); \
	if [ "$$skipped" -gt 0 ]; then \
	  echo "✓ green ($$skipped gates skipped)"; \
	else \
	  echo "✓ all green"; \
	fi

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true

install-dev:
	$(PYTHON) -m pip install -e ./memory
	@echo "ucw-memory installed in editable mode. Try: ucw-memory stats"
