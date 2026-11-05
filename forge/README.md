# check_snclient E2E Test Forge

Production-grade, isolated E2E integration test harness for [`check_snclient`](../) validating black-box execution against live [SNClient](https://github.com/ConSol-Monitoring/snclient) agent daemons and [Tinyproxy](https://tinyproxy.github.io/) HTTP CONNECT forward proxies.

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           check_snclient E2E Forge                          │
├─────────────────┬───────────────────┬───────────────────┬───────────────────┤
│    CLI Engine   │  IaC Provisioner  │  Container Image  │   Test Runner     │
│   Typer & Rich  │  Ansible Playbook │   Packer & Docker │  Pytest/Testinfra │
└─────────────────┴───────────────────┴───────────────────┴───────────────────┘
```

---

## Table of Contents

- [Overview & Philosophy](#overview--philosophy)
- [Quick Start](#quick-start)
- [Architecture & OOP Composition](#architecture--oop-composition)
  - [Appliance & Component Model](#appliance--component-model)
  - [Ansible Mutation Engine](#ansible-mutation-engine)
- [Topology & Placement Matrix](#topology--placement-matrix)
- [Core Developer Workflows](#core-developer-workflows)
  - [1. Local Rapid Dev Loop (Ephemeral Ports)](#1-local-rapid-dev-loop-ephemeral-ports)
  - [2. Feature Branch Testing (Hot-Swappable Slots)](#2-feature-branch-testing-hot-swappable-slots)
  - [3. Hermetic Container Appliance Testing](#3-hermetic-container-appliance-testing)
  - [4. External Target Binding & Safety Lock](#4-external-target-binding--safety-lock)
- [State Contract & Runtime Discovery](#state-contract--runtime-discovery)
- [Live Command Tracing (`▶ Exec`)](#live-command-tracing--exec)
- [CLI Reference](#cli-reference)
- [Makefile Target Matrix](#makefile-target-matrix)
- [Directory Structure](#directory-structure)

---

## Overview & Philosophy

A common question when developing Go monitoring plugins is: *“Why not write integration tests in Go with `httptest.Server` mocks?”*

Unit tests validate internal Go functions in memory. However, `check_snclient` is deployed into production monitoring systems (Nagios, Icinga 2, Naemon, Shinken) as an opaque compiled CLI binary. E2E verification demands true black-box realism:

1. **Protocol Realism vs Synthetic Mocks**: Real HTTP forward proxies (like Tinyproxy in C) manage TCP socket tunnels via `HTTP CONNECT`, negotiate TLS handshakes to backend daemons, and emit `407 Proxy Authentication Required` challenges with protocol quirks that mocks rarely reproduce.
2. **True CLI Contract**: Tests execute the compiled binary exactly as a monitoring daemon would invoke it, verifying arguments parsing, exit status codes (`0` OK, `1` WARNING, `2` CRITICAL, `3` UNKNOWN), stdout formatting, and Nagios performance data (`|'label'=value;warn;crit;min;max`).
3. **Declarative Infrastructure**: Ansible manages system dependencies, binary downloads, self-signed TLS certificates, templating, and port allocation across bare-metal user-space and container builds with zero duplicate logic.
4. **Instant, Isolated Python Environment**: Thanks to [uv](https://github.com/astral-sh/uv), the entire Python virtual environment and its dependencies install in milliseconds in user space, leaving host systems untouched.

---

## Quick Start

You can operate the forge using either the `Makefile` wrapper or the direct `forge` Python CLI:

### Using the Makefile (Standard)

```bash
# 1. Compile check_snclient from repository root
make build

# 2. Run the complete test lifecycle in one shot
make -C forge test-all

# Or run individual steps:
make -C forge up        # Start SNClient and Tinyproxy on dynamic ephemeral ports
make -C forge status    # Display live listening state, socket health, and placement matrix
make -C forge test      # Run Testinfra integration tests with live command tracing
make -C forge down      # Gracefully stop daemons and clean runtime directory
```

### Using the Forge CLI (`uv run forge`)

```bash
cd forge

# Inspect environment paths, toolchain, and binary slot status
uv run forge info

# Run full test lifecycle on local user-space or Docker container:
uv run forge test-all                      # Local bare-metal (default)
uv run forge test-all --backend docker     # Docker container testbed

# Or operate step-by-step:
uv run forge up --backend docker           # Spin up Docker testbed container
uv run forge status                        # Check socket health and active endpoints
uv run forge test                          # Run integration tests (auto caller)
uv run forge down                          # Gracefully stop container and clean runtime
```

---

## Architecture & OOP Composition

The forge is engineered around an object-oriented domain model that decouples **service state** from **execution environments** and delegates all state mutations to Ansible.

### Appliance & Component Model

```mermaid
classDiagram
    class E2EComponent {
        <<abstract>>
        +str name
        +int port
        +str url
        +is_listening() bool
        +export_runtime(Path workdir)
        +to_dict() dict
        +get_status_row() tuple
    }

    class SNClientComponent {
        +str password
        +is_listening() bool
    }

    class ProxyComponent {
        +str url_invalid_auth
        +str username
        +str password
        +is_listening() bool
    }

    class TestbedAppliance {
        <<abstract>>
        +Path workdir
        +dict components
        +get_component(name) E2EComponent
        +is_ready() bool
        +sync_binary_slot(slot_mgr)
    }

    class LocalAppliance {
        +provision(sn_port, tp_port)
        +teardown()
    }

    class DockerAppliance {
        +str container_name
        +start()
        +stop()
        +is_running() bool
    }

    class BoundAppliance {
        +Path lock_file
        +str target_name
        +bind(data)
        +unbind()
    }

    E2EComponent <|-- SNClientComponent
    E2EComponent <|-- ProxyComponent
    TestbedAppliance o-- E2EComponent : contains
    TestbedAppliance <|-- LocalAppliance
    TestbedAppliance <|-- DockerAppliance
    TestbedAppliance <|-- BoundAppliance
```

- **`E2EComponent`** encapsulates endpoints, credentials, socket readiness verification, and runtime metadata export (`runtime/` property files).
  - [`SNClientComponent`](src/forge/components.py): Manages SNClient agent URL, port, REST API password, and TLS socket probing.
  - [`ProxyComponent`](src/forge/components.py): Manages forward proxy URL, valid and invalid credentials for HTTP 407 challenge tests, and proxy port probing.
- **`TestbedAppliance`** aggregates components and represents an execution environment:
  - [`LocalAppliance`](src/forge/appliance.py): User-space background daemons running locally on Linux x86_64 with dynamic ephemeral ports.
  - [`DockerAppliance`](src/forge/appliance.py): Hermetic OCI container running SNClient and Tinyproxy daemons isolated in Debian Slim.
  - [`BoundAppliance`](src/forge/appliance.py): External pre-existing environment locked via [`bind.lock.json`](workdir/bind.lock.json).

### Ansible Mutation Engine

All file and container mutations are strictly handled by Ansible playbooks using official connection plugins rather than custom shell scripts:

```mermaid
flowchart LR
    Caller["CLI / SlotManager\n(forge attach / detach)"]

    subgraph AnsibleEngine["Ansible Convergence Playbooks"]
        Attach["ansible/attach.yml"]
        Detach["ansible/detach.yml"]
    end

    subgraph Targets["Target Environments"]
        LocalHost["Local Workspace\n(-c local)"]
        DockerCont["Docker Container\n(-c community.docker.docker)"]
    end

    Caller --> Attach & Detach
    Attach -->|Symlink / Copy| LocalHost
    Attach -->|Hot-swap inside container| DockerCont
    Detach -->|Reset to default repo build| LocalHost
    Detach -->|Restore container binary| DockerCont
```

- When running on workstation: updates symlinks in `forge/bin/` via `ansible-playbook -c local`.
- When a Docker testbed container is active: hot-swaps `/usr/bin/check_snclient` inside the running container using `ansible-playbook -c community.docker.docker`.

---

## Topology & Placement Matrix

The testbed cleanly decouples **where services run** (`--backend local|docker`) from **where the check is invoked** (`--caller auto|local|docker`):

| Component | Local Backend (`--backend local`) | Docker Backend (`--backend docker`) | External Target (`forge bind`) |
| :--- | :--- | :--- | :--- |
| **SNClient Daemon** | Local user-space (`workdir/snclient/`) | Running inside container (`docker://snclient-testbed`) | Remote host (e.g. `https://corp:8443`) |
| **Tinyproxy Daemon** | Local user-space (`workdir/tinyproxy/`) | Running inside container (`docker://snclient-testbed`) | Remote proxy (e.g. `http://proxy:3128`) |
| **Caller (`--caller`)** | `local`: executed on workstation | `docker`: executed in container via `docker exec`<br>`local`: executed on workstation targeting mapped ports | `local`: executed on workstation targeting remote URLs |

### Caller Placement Modes

- `--caller auto` (default): Automatically selects `docker` when the testbed container is running; otherwise selects `local`.
- `--caller local`: Forces `check_snclient` to run on the workstation host, connecting to either local daemons or Docker container mapped ports (`18443`, `18888`).
- `--caller docker`: Forces Testinfra to connect via `docker://snclient-testbed` and invoke `/usr/bin/check_snclient` directly inside the container.

---

## Core Developer Workflows

### 1. Local Rapid Dev Loop (Ephemeral Ports)

Ideal for daily development on Linux x86_64. Starts in less than a second without requiring Docker or root privileges.

```bash
# Start background daemons on dynamic ephemeral ports (kernel allocated, zero collision)
forge up

# Check socket health and active endpoints
forge status

# Run integration tests
forge test

# Clean shutdown and purge runtime directory
forge down
```

> [!TIP]
> You can also specify static ports when needed:
> `forge up --snclient-port 18443 --tinyproxy-port 18888`

### 2. Feature Branch Testing (Hot-Swappable Slots)

Validate a custom build from a feature branch (e.g. proxy support, new flags) without recompiling or restarting testbeds:

```bash
# Attach custom binary to the forge slot
forge attach ../dist/check_snclient

# Inspect slot status: detects custom link and verifies feature flags (e.g. +proxy)
forge info

# Run tests against the attached binary
forge test

# Reset slot back to default repository binary
forge detach
```

> [!NOTE]
> If a Docker testbed container is currently running, `forge attach` automatically hot-swaps `/usr/bin/check_snclient` inside the container using Ansible.

### 3. Hermetic Container Appliance Testing

Ideal for hermetic CI workflows, macOS, or Windows developers running Docker:

```bash
# 1. Bake immutable container image (Debian Slim with SNClient & Tinyproxy pre-installed)
forge bake

# 2. Run full lifecycle in container appliance
forge test-all --backend docker

# Or step-by-step:
forge up --backend docker
forge test --caller docker    # Test inside container
forge test --caller local     # Test from host targeting container mapped ports
forge down --backend docker
```

### 4. External Target Binding & Safety Lock

Validate pre-existing staging, QA, or corporate infrastructure (e.g. remote SNClient with corporate Squid/Apache forward proxy) without provisioning local services:

```bash
# 1. Lock forge to external environment
forge bind staging \
  --snclient-url https://staging-server:8443 \
  --snclient-pass secret_password \
  --proxy-url http://proxyuser:proxypass@proxy.corp:3128

# Or load definition from a JSON profile
forge bind --file targets/staging-env.json

# 2. Verify reachability & socket health against external endpoints
forge status

# 3. Run integration tests against external target
forge test

# 4. Release lock and restore local/docker capabilities
forge unbind
```

> [!WARNING]
> While locked in `bind` mode, commands that mutate local infrastructure (`forge up`, `forge test-all`) are intentionally blocked by safety guards to prevent accidental daemon provisioning or state clobbering.

---

## State Contract & Runtime Discovery

The forge harness strictly decouples provisioning from test execution through a **loose, configfs-style state contract**:

```text
forge/workdir/
├── bind.lock.json                    # Target Lock metadata (when in bind mode)
├── snclient/
│   └── runtime/                      # Written during provisioning
│       ├── port                      # e.g. 54317
│       ├── url                       # e.g. https://127.0.0.1:54317
│       └── password                  # e.g. secret_forge_pass
└── tinyproxy/
    └── runtime/                      # Written during provisioning
        ├── port                      # e.g. 43747
        ├── url                       # e.g. http://proxyuser:proxypass@127.0.0.1:43747
        ├── url_invalid_auth          # e.g. http://proxyuser:invalidpass@127.0.0.1:43747
        ├── user                      # e.g. proxyuser
        └── password                  # e.g. proxypass
```

- **Zero Runtime Ansible Overhead**: Testinfra tests consume flat property files through strongly-typed dataclasses ([`SNClientRuntime`](src/forge/models.py), [`TinyproxyRuntime`](src/forge/models.py)). Tests execute with zero Ansible subprocess overhead.
- **Dynamic Kernel Ports**: By default, `SNCLIENT_PORT=0` and `TINYPROXY_PORT=0`. Ports are assigned dynamically by the kernel at startup, preventing port collision during parallel runs.

---

## Live Command Tracing (`▶ Exec`)

When running tests, Testinfra prints the exact executed command, its return code, and live stdout/stderr:

```text
tests/test_checks.py::TestSNClientIntegration::test_proxy_traversal_success[local] 
  ▶ Exec : /path/to/check_snclient -k -u https://127.0.0.1:54317 -p secret_forge_pass -proxy http://proxyuser:proxypass@127.0.0.1:43747 check_uptime
  ↳ [RC=1] WARNING - uptime: 1d 04:59h, boot: 2026-11-03 20:05:09 UTC |'uptime'=104341s;172800:;86400:;;
PASSED
```

Every command is reproducible: copy and paste directly into your shell to inspect or debug.

---

## CLI Reference

The unified CLI `forge` provides complete control over testbeds and binary slots:

| Command | Description | Key Options |
| :--- | :--- | :--- |
| `forge up` | Starts testbed daemons (local user-space or Docker container) | `--backend local\|docker`, `-s <port>`, `-p <port>` |
| `forge down` | Gracefully stops testbed daemons and cleans runtime state | `--backend local\|docker`, `-s <port>`, `-p <port>` |
| `forge test` | Runs Testinfra integration tests against active testbed | `--caller auto\|local\|docker`, `-b <path>`, pytest args |
| `forge test-all` | Full lifecycle: provisions testbed, runs tests, and tears down | `--backend local\|docker`, `--caller auto\|local\|docker` |
| `forge status` | Inspects live listening state, socket health, and placement matrix | `--all` (includes toolchain and binary inspection) |
| `forge info` | Displays paths, Target Lock status, binary slots, and versions | `-b <path>` |
| `forge attach` | Attaches a custom binary to the `forge/bin/` slot via symlink | `<target>`, `-c <component>` |
| `forge detach` | Detaches custom slot and resets to default repository binary | `-c <component>` |
| `forge bind` | Locks forge to external pre-existing environment | `-u <url>`, `-p <pass>`, `-P <proxy_url>`, `-f <file>` |
| `forge unbind` | Releases external target lock and restores local/docker capabilities | - |
| `forge bake` | Bakes immutable OCI container image via Packer | - |
| `forge clean` | Cleans up temporary runtime files and test caches | - |

---

## Makefile Target Matrix

A clean, non-intrusive Makefile is provided in `forge/Makefile` for seamless shell operation:

| Target | Description | Parameters & Defaults |
| :--- | :--- | :--- |
| `make -C forge test-all` | Full lifecycle test run | `BACKEND=local\|docker`, `CALLER=auto\|local\|docker` |
| `make -C forge test` | Run integration tests against active testbed | `CALLER=auto\|local\|docker`, `CHECK_BIN=...` |
| `make -C forge up` | Start testbed daemons | `BACKEND=local\|docker`, `SNCLIENT_PORT=0`, `TINYPROXY_PORT=0` |
| `make -C forge down` | Gracefully stop active testbeds | `BACKEND=local\|docker` |
| `make -C forge status` | Inspect live component health & topology | - |
| `make -C forge info` | Inspect tooling versions & binary slots | `CHECK_BIN=...` |
| `make -C forge attach` | Attach custom binary to slot | `BIN=/path/to/binary`, `COMPONENT=check_snclient` |
| `make -C forge detach` | Reset slot to default build | `COMPONENT=check_snclient` |
| `make -C forge bind` | Lock to external target | `TARGET=external`, `FILE=...`, `SNCLIENT_URL=...` |
| `make -C forge unbind` | Release external target lock | - |
| `make -C forge bake` | Bake container image via Packer | - |
| `make -C forge clean` | Clean temporary runtime and cache files | - |
| `make -C forge distclean` | Complete purge (`.venv`, `bin/`, `workdir/`) | - |

---

## Directory Structure

```text
forge/
├── ansible/
│   ├── attach.yml              # Slot synchronization playbook (-c local or -c community.docker.docker)
│   ├── detach.yml              # Slot reset playbook
│   ├── playbook.yml            # Canonical testbed provisioning playbook
│   ├── teardown.yml            # Graceful testbed teardown playbook
│   ├── vars/main.yml           # Default credentials, versions, dynamic ports
│   ├── templates/
│   │   └── entrypoint.sh.j2    # Container appliance entrypoint template
│   └── roles/
│       ├── snclient/           # Deploy & configure SNClient agent daemon
│       └── tinyproxy/          # Deploy & configure Tinyproxy forward proxy
│
├── packer/
│   └── testbed.pkr.hcl         # Packer HCL2 template baking OCI container image
│
├── src/
│   └── forge/
│       ├── __init__.py         # Package entrypoint
│       ├── appliance.py        # OOP Appliance hierarchy (LocalAppliance, DockerAppliance, BoundAppliance)
│       ├── cli.py              # Typer CLI application (rich formatting, help, workflows)
│       ├── components.py       # Modular E2EComponent abstractions (SNClientComponent, ProxyComponent)
│       ├── models.py           # Strongly-typed runtime state dataclasses & TCP socket health probes
│       ├── orchestrator.py     # Pure orchestration engine delegating to appliances & slot managers
│       ├── process.py          # Unified ProcessRunner (zero raw subprocess calls)
│       └── slots.py            # BinarySlot & SlotManager handling feature probes (+proxy)
│
├── tests/
│   ├── conftest.py             # Pytest fixtures consuming active appliance & runtime properties
│   └── test_checks.py          # Testinfra assertions with live command tracing (▶ Exec)
│
├── Makefile                    # Makefile wrapper delegating to uv run forge
└── pyproject.toml              # uv & hatchling project definition
```
