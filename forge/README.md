# check_snclient E2E Forge

Integration test harness for `check_snclient` against a live [SNClient](https://github.com/ConSol-Monitoring/snclient) agent and [Tinyproxy](https://tinyproxy.github.io/) forward proxy.

- **Ansible**: Declarative, idempotent testbed provisioning (`playbook.yml`, `teardown.yml`).
- **Packer**: Automated OCI container image baking (`testbed.pkr.hcl`) re-using the Ansible playbook.
- **Testinfra**: Fast test execution & assertions (`test_checks.py`).
- **uv**: Pure user-space dependency management (`pytest-testinfra`, `ansible-core`).

## Quick Start

```bash
# 1. Compile check_snclient binary (from repo root)
make build

# 2. Run the full lifecycle (provision testbed -> run tests -> tear down)
make -C forge test-all

# Or run individual steps:
make -C forge up        # Start SNClient & Tinyproxy on dynamic ports
make -C forge test      # Run Testinfra integration tests
make -C forge down      # Stop daemons and clean runtime directory
```

## Architecture & State Contract

The harness strictly decouples infrastructure provisioning from test validation:
1. **Ansible manages infrastructure**: Each component publishes its connection metadata in configfs-style files under its own `runtime/` directory:
   - `workdir/snclient/runtime/` : `port`, `url`, `password`.
   - `workdir/tinyproxy/runtime/` : `port`, `url`, `url_invalid_auth`, `user`, `password`.
2. **Testinfra consumes state**: `conftest.py` discovers these runtime properties via strongly-typed `ComponentRuntime` dataclasses without invoking any Ansible subprocesses.
3. **Dynamic ports by default**:
   - Defaults to `SNCLIENT_PORT=0` and `TINYPROXY_PORT=0`: the kernel assigns available ephemeral ports (zero collision).
   - Static ports can be forced when needed (e.g. `make -C forge up SNCLIENT_PORT=18443 TINYPROXY_PORT=18888`).

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
  - **Benefits:** Maximum speed (tests complete in seconds), no Docker daemon required, complete cleanup in one command (`make down`).
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
| `make -C forge status` | Inspects listening state of components via `runtime/port` |
| `make -C forge bake` | Builds Docker image via Packer (`check_snclient-testbed:latest`) |
| `make -C forge container-run` | Starts baked Docker testbed in background |
| `make -C forge container-stop` | Stops Docker testbed container |
| `make -C forge clean` | Stops daemons and clears temporary caches |
| `make -C forge distclean` | Full wipe including `.venv` and `bin/` |

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
├── conftest.py                 # Pytest configuration & typed ComponentRuntime accessors
├── test_checks.py              # Testinfra assertions (Host)
├── Makefile                    # Unified lifecycle management
└── pyproject.toml              # uv project declaration
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
