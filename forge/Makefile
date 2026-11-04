SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

FORGE_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
REPO_ROOT := $(shell dirname $(FORGE_DIR))
UV ?= uv
PACKER := $(FORGE_DIR)/bin/packer
VENV := $(FORGE_DIR)/.venv
ANSIBLE_PLAYBOOK := $(VENV)/bin/ansible-playbook
PYTEST := $(VENV)/bin/pytest

SNCLIENT_PORT ?= 0
TINYPROXY_PORT ?= 0

IMAGE_TAG := check_snclient-testbed:latest
CONTAINER_NAME := snclient-testbed

.PHONY: help
help: ## Show this help message
	@echo "check_snclient Test Forge - Management Makefile"
	@echo ""
	@echo "Usage: make [target] [SNCLIENT_PORT=0|port] [TINYPROXY_PORT=0|port]"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --- Python Environment & Tooling ---

.PHONY: setup
setup: $(VENV)/bin/pytest ## Create/sync Python virtual environment with uv
$(VENV)/bin/pytest: $(FORGE_DIR)/pyproject.toml $(FORGE_DIR)/uv.lock
	@echo "==> Syncing Python virtual environment with uv..."
	@cd $(FORGE_DIR) && $(UV) sync

.PHONY: packer-init
packer-init: ## Download and initialize local Packer binary and plugins
	@if [ ! -x "$(PACKER)" ]; then \
		echo "==> Downloading Packer into $(FORGE_DIR)/bin..."; \
		mkdir -p $(FORGE_DIR)/bin; \
		curl -fsSL https://releases.hashicorp.com/packer/1.11.2/packer_1.11.2_linux_amd64.zip -o /tmp/packer.zip; \
		unzip -o /tmp/packer.zip -d $(FORGE_DIR)/bin; \
		rm -f /tmp/packer.zip; \
		chmod +x $(PACKER); \
	fi
	@echo "==> Initializing Packer plugins..."
	@$(PACKER) init $(FORGE_DIR)/packer/testbed.pkr.hcl

# --- Local Testbed Lifecycle (Ansible) ---

.PHONY: up
up: setup ## Provision and start SNClient & Tinyproxy locally (supports SNCLIENT_PORT=0)
	@echo "==> Starting local testbed daemons (snclient: $(SNCLIENT_PORT), tinyproxy: $(TINYPROXY_PORT))..."
	@cd $(FORGE_DIR) && SNCLIENT_PORT=$(SNCLIENT_PORT) TINYPROXY_PORT=$(TINYPROXY_PORT) \
		$(ANSIBLE_PLAYBOOK) -i 'localhost,' -c local ansible/playbook.yml

.PHONY: down
down: setup ## Stop local testbed daemons and clean runtime directory
	@echo "==> Stopping local testbed daemons..."
	@cd $(FORGE_DIR) && SNCLIENT_PORT=$(SNCLIENT_PORT) TINYPROXY_PORT=$(TINYPROXY_PORT) \
		$(ANSIBLE_PLAYBOOK) -i 'localhost,' -c local ansible/teardown.yml

.PHONY: status
status: ## Check if local testbed components are listening
	@for comp in snclient tinyproxy; do \
		pfile="$(FORGE_DIR)/workdir/$$comp/runtime/port"; \
		if [ -f "$$pfile" ]; then \
			port=$$(cat "$$pfile"); \
			if ss -tulpn | grep -q ":$$port "; then \
				echo -e "[OK] $$comp listening on port $$port"; \
			else \
				echo -e "[DOWN] $$comp not listening on port $$port"; \
			fi; \
		else \
			echo "[-] $$comp not running (missing $$pfile)"; \
		fi; \
	done

# --- Testinfra Validation ---

.PHONY: test
test: setup ## Run Testinfra integration tests against active testbed
	@echo "==> Running Testinfra integration tests..."
	@cd $(FORGE_DIR) && $(UV) run pytest -v $(if $(CHECK_BIN),--check-bin $(CHECK_BIN),) $(ARGS)

.PHONY: test-all
test-all: setup ## Start testbed, run tests, and tear down (supports SNCLIENT_PORT=0)
	@$(MAKE) up SNCLIENT_PORT=$(SNCLIENT_PORT) TINYPROXY_PORT=$(TINYPROXY_PORT)
	@$(MAKE) test CHECK_BIN=$(CHECK_BIN) ARGS="$(ARGS)"
	@$(MAKE) down

# --- Docker & Packer Image Baking ---

.PHONY: bake
bake: packer-init setup ## Bake Docker image via Packer (check_snclient-testbed:latest)
	@echo "==> Baking Docker image via Packer..."
	@$(PACKER) build $(FORGE_DIR)/packer/testbed.pkr.hcl

.PHONY: container-run
container-run: ## Run baked Docker testbed in background
	@echo "==> Starting Docker testbed container ($(CONTAINER_NAME))..."
	@docker run -d --rm --name $(CONTAINER_NAME) -p 18443:18443 -p 18888:18888 $(IMAGE_TAG)
	@docker ps --filter "name=$(CONTAINER_NAME)"

.PHONY: container-stop
container-stop: ## Stop Docker testbed container
	@echo "==> Stopping Docker testbed container..."
	@docker stop $(CONTAINER_NAME) 2>/dev/null || true

# --- Cleanup ---

.PHONY: clean
clean: down ## Stop daemons and clean temporary test files and caches
	@echo "==> Cleaning caches and ephemeral files..."
	@rm -rf $(FORGE_DIR)/workdir $(FORGE_DIR)/.pytest_cache $(FORGE_DIR)/__pycache__

.PHONY: distclean
distclean: clean ## Full wipe including .venv and downloaded binaries
	@echo "==> Wiping virtualenv and bin/..."
	@rm -rf $(FORGE_DIR)/.venv $(FORGE_DIR)/bin
