SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

FORGE_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
UV ?= uv
VENV := $(FORGE_DIR)/.venv

SNCLIENT_PORT ?= 0
TINYPROXY_PORT ?= 0
CHECK_BIN ?=
ARGS ?=

.PHONY: help
help: ## Show this help message
	@echo "check_snclient Test Forge - Management Makefile"
	@echo ""
	@echo "Usage: make [target] [SNCLIENT_PORT=0|port] [TINYPROXY_PORT=0|port] [CHECK_BIN=/path/to/bin]"
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

# --- Local Testbed Lifecycle (Unified Forge CLI) ---

.PHONY: up
up: setup ## Provision and start SNClient & Tinyproxy locally (supports SNCLIENT_PORT=0)
	@cd $(FORGE_DIR) && $(UV) run forge up --snclient-port $(SNCLIENT_PORT) --tinyproxy-port $(TINYPROXY_PORT)

.PHONY: down
down: setup ## Stop local testbed daemons and clean runtime directory
	@cd $(FORGE_DIR) && $(UV) run forge down --snclient-port $(SNCLIENT_PORT) --tinyproxy-port $(TINYPROXY_PORT)

.PHONY: status
status: setup ## Check if local testbed components are listening
	@cd $(FORGE_DIR) && $(UV) run forge status

.PHONY: info
info: setup ## Display environment paths, workdir state, and installed binaries
	@cd $(FORGE_DIR) && $(UV) run forge info

.PHONY: test
test: setup ## Run Testinfra integration tests against active testbed
	@cd $(FORGE_DIR) && $(UV) run forge test $(if $(CHECK_BIN),--check-bin "$(CHECK_BIN)",) $(ARGS)

.PHONY: test-all
test-all: setup ## Start testbed, run tests, and tear down (supports SNCLIENT_PORT=0)
	@cd $(FORGE_DIR) && $(UV) run forge test-all --snclient-port $(SNCLIENT_PORT) --tinyproxy-port $(TINYPROXY_PORT) $(if $(CHECK_BIN),--check-bin "$(CHECK_BIN)",)

# --- Docker & Packer Image Baking ---

.PHONY: bake
bake: packer-init setup ## Bake Docker image via Packer (check_snclient-testbed:latest)
	@cd $(FORGE_DIR) && $(UV) run forge bake

.PHONY: container-run
container-run: setup ## Run baked Docker testbed in background
	@cd $(FORGE_DIR) && $(UV) run forge container-run

.PHONY: container-stop
container-stop: setup ## Stop Docker testbed container
	@cd $(FORGE_DIR) && $(UV) run forge container-stop

# --- Cleanup ---

.PHONY: clean
clean: setup ## Stop daemons and clean temporary test files and caches
	@cd $(FORGE_DIR) && $(UV) run forge clean

.PHONY: distclean
distclean: ## Full wipe including .venv and downloaded binaries
	@echo "==> Wiping virtualenv and bin/..."
	@rm -rf $(FORGE_DIR)/.venv $(FORGE_DIR)/bin $(FORGE_DIR)/workdir
