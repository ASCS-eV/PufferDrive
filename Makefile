# Compatibility shim: the justfile is the source of truth. Run `just --list`.
JUST ?= just

TARGETS := help test build rebuild test-unit test-eval test-c test-notebooks \
	test-docker-smoke perf clean ensure-test-deps check-venv

# Preserves the old `make PYTHON=/path/to/python test` override.
ifdef PYTHON
JUST_ARGS := python=$(PYTHON)
endif

.DEFAULT_GOAL := help
.PHONY: $(TARGETS)

$(TARGETS):
	@$(JUST) $(JUST_ARGS) $@
