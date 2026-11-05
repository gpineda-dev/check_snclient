SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

FORGE_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
UV ?= uv
VENV := $(FORGE_DIR)/.venv

SNCLIENT_PORT ?= 0
TINYPROXY_PORT ?= 0
CHECK_BIN ?=
BACKEND ?= local
CALLER ?= auto
ARGS ?=

.PHONY: help
help: ## Show this help message
	@echo "check_snclient Test Forge - Management Makefile"
	@echo ""
	@echo "Usage: make [target] [BACKEND=local|docker] [CALLER=auto|local|docker] [CHECK_BIN=/path/to/bin]"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --- Python Environment & Tooling ---

.PHONY: setup
setup: $(VENV)/bin/forge ## Create/sync Python virtual environment with uv
$(VENV)/bin/forge: $(FORGE_DIR)/pyproject.toml $(FORGE_DIR)/uv.lock
	@echo "==> Syncing Python virtual environment with uv..."
	@cd $(FORGE_DIR) && $(UV) sync

.PHONY: packer-init
packer-init: ## Download and initialize local Packer binary and plugins
	@PACKER="$(FORGE_DIR)/bin/packer"; \
	if [ ! -x "$$PACKER" ]; then \
		echo "==> Downloading Packer into $(FORGE_DIR)/bin..."; \
		mkdir -p $(FORGE_DIR)/bin; \
		curl -fsSL https://releases.hashicorp.com/packer/1.11.2/packer_1.11.2_linux_amd64.zip -o /tmp/packer.zip; \
		unzip -o /tmp/packer.zip -d $(FORGE_DIR)/bin; \
		rm -f /tmp/packer.zip; \
		chmod +x "$$PACKER"; \
	fi; \
	echo "==> Initializing Packer plugins..."; \
	$$PACKER init $(FORGE_DIR)/packer/testbed.pkr.hcl

# --- Testbed Lifecycle (Unified Forge CLI) ---

.PHONY: up
up: setup ## Start testbed daemons (Usage: make up [BACKEND=local|docker])
	@cd $(FORGE_DIR) && $(UV) run forge up --backend $(BACKEND) --snclient-port $(SNCLIENT_PORT) --tinyproxy-port $(TINYPROXY_PORT)

.PHONY: down
down: setup ## Stop active testbed daemons and clean runtime directory
	@cd $(FORGE_DIR) && $(UV) run forge down $(if $(filter-out local,$(BACKEND)),--backend $(BACKEND),)

.PHONY: status
status: setup ## Check if testbed components are listening
	@cd $(FORGE_DIR) && $(UV) run forge status $(if $(CHECK_BIN),--check-bin "$(CHECK_BIN)",)

.PHONY: info
info: setup ## Display environment paths, workdir state, and installed binaries
	@cd $(FORGE_DIR) && $(UV) run forge info $(if $(CHECK_BIN),--check-bin "$(CHECK_BIN)",)

.PHONY: attach
attach: setup ## Attach a custom binary under test (Usage: make -C forge attach BIN=/path/to/bin)
	@if [ -z "$(BIN)" ]; then \
		echo "Error: BIN is required. Usage: make -C forge attach BIN=/path/to/check_snclient"; exit 1; \
	fi
	@cd $(FORGE_DIR) && $(UV) run forge attach "$(BIN)"

.PHONY: detach
detach: setup ## Detach custom binary and reset to default repo build
	@cd $(FORGE_DIR) && $(UV) run forge detach

.PHONY: bind
bind: setup ## Lock forge to external target (Usage: make bind NAME=staging URL=https://... PASS=... [PROXY=...])
	@if [ -z "$(URL)" ] || [ -z "$(PASS)" ]; then \
		echo "Error: URL and PASS are required. Usage: make -C forge bind NAME=staging URL=https://remote:8443 PASS=secret [PROXY=http://...]"; exit 1; \
	fi
	@cd $(FORGE_DIR) && $(UV) run forge bind "$(or $(NAME),external)" --snclient-url "$(URL)" --snclient-pass "$(PASS)" $(if $(PROXY),--proxy-url "$(PROXY)",)

.PHONY: unbind
unbind: setup ## Release external target lock
	@cd $(FORGE_DIR) && $(UV) run forge unbind

.PHONY: test
test: setup ## Run Testinfra integration tests against active testbed
	@cd $(FORGE_DIR) && $(UV) run forge test --caller $(CALLER) $(if $(CHECK_BIN),--check-bin "$(CHECK_BIN)",) $(ARGS)

.PHONY: test-all
test-all: setup ## Start testbed, run tests, and tear down (Usage: make test-all [BACKEND=local|docker] [CALLER=auto|local|docker])
	@cd $(FORGE_DIR) && $(UV) run forge test-all --backend $(BACKEND) --caller $(CALLER) --snclient-port $(SNCLIENT_PORT) --tinyproxy-port $(TINYPROXY_PORT) $(if $(CHECK_BIN),--check-bin "$(CHECK_BIN)",) $(ARGS)

# --- Docker & Packer Image Baking ---

.PHONY: bake
bake: packer-init setup ## Bake Docker image via Packer (check_snclient-testbed:latest)
	@cd $(FORGE_DIR) && $(UV) run forge bake

.PHONY: container-run
container-run: setup ## Alias for 'make up BACKEND=docker'
	@$(MAKE) up BACKEND=docker

.PHONY: container-stop
container-stop: setup ## Alias for 'make down BACKEND=docker'
	@$(MAKE) down BACKEND=docker

.PHONY: container-test
container-test: setup ## Alias for 'make test-all BACKEND=docker'
	@$(MAKE) test-all BACKEND=docker CALLER=$(CALLER) $(if $(CHECK_BIN),CHECK_BIN="$(CHECK_BIN)",)

# --- Cleanup ---

.PHONY: clean
clean: setup ## Stop daemons and clean temporary test files and caches
	@cd $(FORGE_DIR) && $(UV) run forge clean

.PHONY: distclean
distclean: ## Full wipe including .venv and downloaded binaries
	@echo "==> Wiping virtualenv and bin/..."
	@rm -rf $(FORGE_DIR)/.venv $(FORGE_DIR)/bin $(FORGE_DIR)/workdir
