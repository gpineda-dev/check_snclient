# check_snclient E2E Forge

Integration test harness for `check_snclient` against a live [SNClient](https://github.com/ConSol-Monitoring/snclient) agent and [Tinyproxy](https://tinyproxy.github.io/) forward proxy.

- **Unified CLI (`forge`)**: Modern Python CLI powered by [Typer](https://typer.tiangolo.com/) and [Rich](https://github.com/Textualize/rich) (`forge up`, `forge status`, `forge info`, `forge test`).
- **Ansible**: Declarative, idempotent testbed provisioning (`playbook.yml`, `teardown.yml`).
- **Packer**: Automated OCI container image baking (`testbed.pkr.hcl`) re-using the Ansible playbook.
- **Testinfra**: Fast test execution & assertions (`test_checks.py`) with live command tracing (`▶ Exec`).
- **uv**: Pure user-space dependency management and virtual environment orchestration.

## Quick Start

You can operate the forge either through the canonical `Makefile` or directly via the `forge` Python CLI:

### Via Makefile

```bash
# 1. Compile check_snclient binary (from repo root)
make build

# 2. Run the full lifecycle (provision testbed -> run tests -> tear down)
make -C forge test-all

# Or run individual steps:
make -C forge up        # Start SNClient & Tinyproxy on dynamic ephemeral ports
make -C forge status    # Display live listening state and socket health
make -C forge info      # Display environment paths and detected toolchain binaries
make -C forge test      # Run Testinfra integration tests with live command tracing
make -C forge down      # Stop daemons and clean runtime directory
```

### Via Forge CLI (`uv run forge`)

```bash
cd forge

# Inspect toolchain and environment
uv run forge info

# Provision testbed with dynamic ephemeral ports
uv run forge up

# Check status with environment summary
uv run forge status --all

# Test a custom binary
uv run forge test --check-bin /path/to/check_snclient

# Clean shutdown
uv run forge down
```

## Architecture & State Contract

The harness strictly decouples infrastructure provisioning from test validation:
1. **Ansible manages infrastructure**: Each component publishes its connection metadata in configfs-style flat files under its own `runtime/` directory:
   - `workdir/snclient/runtime/` : `port`, `url`, `password`.
   - `workdir/tinyproxy/runtime/` : `port`, `url`, `url_invalid_auth`, `user`, `password`.
2. **Python domain models consume state**: `forge.models` discovers runtime properties via strongly-typed `ComponentRuntime` dataclasses (`SNClientRuntime`, `TinyproxyRuntime`) with zero Ansible subprocess overhead.
3. **Dynamic ports by default**:
   - Defaults to `SNCLIENT_PORT=0` and `TINYPROXY_PORT=0`: the kernel assigns available ephemeral ports (zero port collision during parallel or CI runs).
   - Static ports can be forced when needed (e.g. `make -C forge up SNCLIENT_PORT=18443 TINYPROXY_PORT=18888`).

## Design Rationale: Why Ansible & Python/Testinfra?

A common question when testing a Go project is: *“Why not write the entire integration harness in Go?”*

While unit tests for `check_snclient` are naturally written in Go (`go test`), E2E integration testing has fundamentally different requirements:

### 1. Black-Box Verification vs In-Memory Mocks
- In-memory Go mocks (`httptest.Server`) test our *assumptions* of how proxies and agents behave, not how they act in production.
- Real-world forward proxies (like Tinyproxy in C) handle `HTTP CONNECT` tunneling, TLS handshakes, and `407 Proxy Authentication Required` challenges with subtle protocol quirks that mocks rarely replicate accurately.
- E2E testing must treat `check_snclient` as an opaque CLI binary executed exactly as a Nagios / Icinga / Naemon core would invoke it on a production system (verifying stdout, stderr, perfdata formatting, and standard exit codes: 0, 1, 2, 3).

### 2. Declarative Infrastructure vs Imperative Daemon Wrangling
- Orchestrating multi-service integration environments in pure Go requires hundreds of lines of imperative, brittle boilerplate: downloading binaries, generating self-signed TLS certificates, templating config files, managing background subprocesses (`os/exec`), handling signals (`SIGTERM`), polling sockets for readiness, and guaranteeing cleanup on failure.
- Ansible provides declarative, battle-tested primitives for all of these tasks out-of-the-box (`ansible.builtin.template`, `wait_for`, `get_url`, etc.).
- **Code Reuse (DRY):** The exact same Ansible playbook provisions local bare-metal developer machines *and* bakes container images via Packer without duplicating a single line of provisioning logic.

### 3. Testinfra & uv: Zero Friction, Maximum Clarity
- **Pytest + Testinfra** (`pytest-testinfra`) is an industry reference for system-level verification, offering expressive fixtures, parameterized test matrices, and rich failure diffs with zero assertion boilerplate.
- Thanks to **uv**, the Python virtual environment and dependencies are resolved and installed in milliseconds in user space, with zero system-wide pollution and zero impact on the developer's host toolchain.
- The loose, file-based state contract (`runtime/` property files) ensures the test suite does not execute Ansible subprocesses at runtime: tests remain blazing fast, typed, and clean.

## Live Command Tracing (`▶ Exec`)

When executing tests, the harness prints the exact CLI command invoked alongside its return code and output:

```text
tests/test_checks.py::TestSNClientIntegration::test_proxy_traversal_success[local] 
  ▶ Exec : /path/to/check_snclient -k -u https://127.0.0.1:54317 -p secret_forge_pass -proxy http://proxyuser:proxypass@127.0.0.1:43747 check_uptime
  ↳ [RC=1] WARNING - uptime: 1d 04:59h, boot: 2026-11-03 20:05:09 UTC |'uptime'=104341s;172800:;86400:;;
PASSED
```

This guarantees full auditability: any command can be copied and pasted directly into a developer shell to reproduce or inspect behavior.

## Two Execution Modes: Local Ansible vs Packer Image

The forge offers two ways to run the testbed, both powered by the **exact same Ansible playbook** (zero code duplication):

```text
               ┌────────────────────────────────────────────────────────┐
               │    Canonical Recipe : forge/ansible/playbook.yml       │
               └───────────────────────────┬────────────────────────────┘
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    ▼                                             ▼
        [Mode 1 : Local Ansible]                      [Mode 2 : Packer Image]
           make up / test-all                                make bake
                    │                                             │
      • Direct user-space execution                 • OCI packaging (Debian Slim)
      • Instant startup (1-2s)                      • Universal execution via Docker
      • Dynamic ports (zero collision)              • Immutable & hermetic
      • Ideal for Linux local dev loop              • Ideal for Cloud CI, macOS, Windows
```

### When to use which mode?

- **Mode 1: Local Ansible (`make -C forge up` or `test-all`) — Recommended for daily dev**
  - **Audience:** Developers on Linux x86_64 machines.
  - **Benefits:** Maximum speed (tests complete in ~0.25s), no Docker daemon required, complete cleanup in one command (`make down`).
- **Mode 2: Packer Image (`make -C forge bake`) — Portability & CI bonus**
  - **Audience:** Developers on macOS (ARM64) or Windows (where ELF x86_64 binaries do not run natively on host), or Cloud CI workflows (GitHub Actions).
  - **Benefits:** Run the testbed in a standardized container with `docker run` or instant `docker pull`, avoiding package downloads and certificate generation on every CI commit.
  - **Maintenance Cost:** Zero. The Packer template delegates provisioning entirely to the local Ansible playbook.

## Makefile Targets

| Target | Description |
| :--- | :--- |
| `make -C forge test-all` | Full lifecycle: provisions testbed, runs tests, and tears down |
| `make -C forge test` | Runs pytest-testinfra against the currently active testbed |
| `make -C forge test CHECK_BIN=...` | Tests a custom binary (e.g. from a feature branch) |
| `make -C forge up` | Provisions and starts SNClient & Tinyproxy (supports `SNCLIENT_PORT=0`) |
| `make -C forge down` | Gracefully stops daemons and removes `workdir/` |
| `make -C forge status` | Inspects live listening state and socket health of components |
| `make -C forge info` | Displays environment paths, workdir state, and detected binaries |
| `make -C forge bake` | Builds Docker image via Packer (`check_snclient-testbed:latest`) |
| `make -C forge container-run` | Starts baked Docker testbed in background |
| `make -C forge container-stop` | Stops Docker testbed container |
| `make -C forge clean` | Stops daemons and clears temporary caches |
| `make -C forge distclean` | Full wipe including `.venv`, `bin/`, and `workdir/` |

## Structure

```text
forge/
├── ansible/
│   ├── playbook.yml            # Canonical testbed provisioning playbook
│   ├── teardown.yml            # Clean shutdown of testbed services
│   ├── vars/main.yml           # Ports, credentials, versions
│   ├── templates/
│   │   └── entrypoint.sh.j2    # Jinja2 template for container entrypoint
│   └── roles/
│       ├── snclient/           # Deploy & configure official SNClient daemon
│       └── tinyproxy/          # Deploy & configure Tinyproxy forward proxy
│
├── packer/
│   └── testbed.pkr.hcl         # Packer HCL2 template to bake an immutable Docker image
│
├── src/
│   └── forge/
│       ├── __init__.py         # Package entrypoint
│       ├── cli.py              # Typer CLI application (up, down, status, info, test, etc.)
│       ├── models.py           # Strongly-typed ComponentRuntime dataclasses & socket health
│       └── orchestrator.py     # Subprocess orchestrator for Ansible, Pytest, and Packer
│
├── tests/
│   ├── conftest.py             # Pytest fixtures consuming forge.models
│   └── test_checks.py          # Testinfra assertions with live command tracing (▶ Exec)
├── Makefile                    # Clean wrapper calling 'uv run forge <cmd>'
└── pyproject.toml              # uv & hatchling project declaration (forge CLI)
```

## Manual Workflows

### 1. Ansible Testbed (Local / Direct)

```bash
# Provision testbed manually
uv run --directory forge ansible-playbook -i "localhost," -c local ansible/playbook.yml

# Tear down testbed manually
uv run --directory forge ansible-playbook -i "localhost," -c local ansible/teardown.yml
```

### 2. Packer (Image Baking)

To build a standalone, immutable testbed Docker image re-using the exact same Ansible playbook:

```bash
forge/bin/packer build forge/packer/testbed.pkr.hcl
```

### 3. Test Any Binary

```bash
# Test a custom check_snclient binary (e.g. from a feature branch)
uv run --directory forge pytest -v --check-bin /path/to/check_snclient
```
