from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Any

from forge.appliance import (
    ApplianceRegistry,
    BoundAppliance,
    DockerAppliance,
    LocalAppliance,
    TestbedAppliance,
)
from forge.components import E2EComponent, ProxyComponent, SNClientComponent
from forge.process import ProcessRunner
from forge.slots import BinarySlot, SlotManager, SlotStatus


FORGE_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = FORGE_DIR.parent
WORKDIR = FORGE_DIR / "workdir"
ANSIBLE_DIR = FORGE_DIR / "ansible"
BIN_DIR = FORGE_DIR / "bin"
PACKER_BIN = BIN_DIR / "packer"
PACKER_TEMPLATE = FORGE_DIR / "packer" / "testbed.pkr.hcl"

IMAGE_TAG = "check_snclient-testbed:latest"
CONTAINER_NAME = "snclient-testbed"
BIND_LOCK_FILE = WORKDIR / "bind.lock.json"

# Shared domain services
slot_manager = SlotManager(bin_dir=BIN_DIR, repo_root=REPO_ROOT)


# --- Binary Slot Management ---


def attach_binary(target_path: Path | str, component: str = "check_snclient") -> Path:
    """Attaches a binary to the slot and propagates to active container if needed."""
    slot = slot_manager.attach(target_path, component=component)
    active = ApplianceRegistry.get_active(WORKDIR, CONTAINER_NAME)
    if active:
        active.sync_binary(component, slot.resolved_path)
    return slot.resolved_path


def detach_binary(component: str = "check_snclient") -> Path | None:
    """Detaches custom binary slot and resets to default repository build."""
    slot = slot_manager.detach(component=component)
    active = ApplianceRegistry.get_active(WORKDIR, CONTAINER_NAME)
    if isinstance(active, DockerAppliance):
        active.reset_binary(component, slot.resolved_path)
    return slot.resolved_path


def get_slot_info(component: str = "check_snclient") -> dict[str, Any]:
    """Inspects binary slot and returns backward-compatible metadata dictionary."""
    slot = slot_manager.get_slot(component)
    return {
        "slot_path": slot.slot_path,
        "target_path": slot.target_path,
        "resolved_path": slot.resolved_path,
        "status": slot.status.value,
        "is_custom": slot.is_custom,
    }


# --- Target Lock & External Binding ---


def is_bound() -> bool:
    """Checks whether the forge is currently locked to an external target."""
    return BoundAppliance.is_locked(WORKDIR)


def get_bind_info() -> dict[str, Any] | None:
    """Reads metadata from the bind lock file if present."""
    app = BoundAppliance.from_lock_file(WORKDIR)
    if not app:
        return None
    sn_comp = app.get_component("SNClient")
    tp_comp = app.get_component("Tinyproxy")
    return {
        "target_name": app.name,
        "source_file": app.source_file,
        "snclient": sn_comp.to_dict() if sn_comp else None,
        "proxy": tp_comp.to_dict() if tp_comp else None,
    }


def bind_target(
    name: str,
    snclient_url: str,
    snclient_password: str,
    proxy_url: str | None = None,
    proxy_invalid_auth_url: str | None = None,
    proxy_user: str | None = None,
    proxy_password: str | None = None,
    source_file: str | None = None,
) -> dict[str, Any]:
    """Locks the forge to an external target via modular E2EComponents."""
    sn_comp = SNClientComponent(url=snclient_url, password=snclient_password)
    components: list[E2EComponent] = [sn_comp]

    if proxy_url:
        tp_comp = ProxyComponent(
            url=proxy_url,
            user=proxy_user,
            password=proxy_password,
            url_invalid_auth=proxy_invalid_auth_url,
        )
        components.append(tp_comp)

    app = BoundAppliance(
        workdir=WORKDIR,
        target_name=name,
        components=components,
        source_file=source_file,
    )
    app.start()
    return get_bind_info() or {}


def unbind_target() -> str | None:
    """Removes the target lock and clears runtime properties."""
    app = BoundAppliance.from_lock_file(WORKDIR)
    name = app.name if app else None
    if app:
        app.stop()
    clean()
    return name


# --- Testbed Appliances Lifecycle ---


def is_container_running() -> bool:
    """Checks if the Docker testbed container is currently running."""
    return DockerAppliance(WORKDIR, container_name=CONTAINER_NAME).is_running()


def run_container() -> int:
    """Starts the Docker testbed appliance and syncs custom binary if attached."""
    if is_bound():
        info = get_bind_info() or {}
        name = info.get("target_name", "unknown")
        raise RuntimeError(
            f"Forge is LOCKED in BIND mode to external target '{name}'. "
            "Run 'forge unbind' first to release the lock."
        )

    app = DockerAppliance(
        workdir=WORKDIR,
        container_name=CONTAINER_NAME,
        image_tag=IMAGE_TAG,
    )
    app.start()

    # Sync attached custom binary to container slot if one is active on host
    slot = slot_manager.get_slot("check_snclient")
    if slot.is_custom and slot.resolved_path:
        app.sync_binary("check_snclient", slot.resolved_path)

    return 0


def stop_container() -> int:
    """Stops the running Docker container and cleans local runtime metadata."""
    app = DockerAppliance(workdir=WORKDIR, container_name=CONTAINER_NAME)
    app.stop()
    clean()
    return 0


def provision(snclient_port: int = 0, tinyproxy_port: int = 0) -> int:
    """Runs the LocalAppliance provisioning via Ansible."""
    if is_bound():
        info = get_bind_info() or {}
        name = info.get("target_name", "unknown")
        raise RuntimeError(
            f"Forge is LOCKED in BIND mode to external target '{name}'. "
            "Run 'forge unbind' first to release the lock."
        )

    app = LocalAppliance(
        workdir=WORKDIR,
        ansible_dir=ANSIBLE_DIR,
        snclient_port=snclient_port,
        tinyproxy_port=tinyproxy_port,
    )
    app.start()
    return 0


def teardown(snclient_port: int = 0, tinyproxy_port: int = 0) -> int:
    """Runs the LocalAppliance teardown via Ansible."""
    app = LocalAppliance(
        workdir=WORKDIR,
        ansible_dir=ANSIBLE_DIR,
        snclient_port=snclient_port,
        tinyproxy_port=tinyproxy_port,
    )
    app.stop()
    return 0


# --- Pytest & Packer Runners ---


def get_pytest_bin() -> str:
    """Locates pytest within the virtualenv or PATH."""
    venv_bin = FORGE_DIR / ".venv" / "bin" / "pytest"
    found = ProcessRunner.find_executable("pytest", preferred_paths=[venv_bin])
    return str(found) if found else "pytest"


def run_tests(
    caller: str = "auto",
    check_bin: str | None = None,
    extra_args: list[str] | None = None,
) -> int:
    """Executes pytest-testinfra against the active testbed with configurable caller placement."""
    resolved_caller = caller.lower()
    if resolved_caller in ("host", "local"):
        resolved_caller = "local"
    elif resolved_caller == "auto":
        resolved_caller = (
            "local" if is_bound() else ("docker" if is_container_running() else "local")
        )

    cmd = [get_pytest_bin(), "tests"]

    if resolved_caller == "docker":
        cmd.append(f"--hosts=docker://{CONTAINER_NAME}")
        bin_arg = check_bin or "/usr/bin/check_snclient"
        cmd.extend(["--check-bin", bin_arg])
    else:
        cmd.append("--hosts=local://")
        if check_bin:
            cmd.extend(["--check-bin", check_bin])

    if extra_args:
        cmd.extend(extra_args)

    env = {}
    if check_bin:
        env["CHECK_SNCLIENT_BIN"] = check_bin
    return ProcessRunner.run(cmd, cwd=FORGE_DIR, env=env)


def bake_image() -> int:
    """Builds the standalone Docker testbed image via Packer."""
    if not PACKER_BIN.is_file() or not os.access(PACKER_BIN, os.X_OK):
        raise RuntimeError(
            f"Packer binary not found at {PACKER_BIN}. Run 'make packer-init' first."
        )
    return ProcessRunner.run([str(PACKER_BIN), "build", str(PACKER_TEMPLATE)], cwd=FORGE_DIR)


def container_test(
    caller: str = "docker",
    check_bin: str | None = None,
    extra_args: list[str] | None = None,
) -> int:
    """Executes the full test lifecycle against a Docker container testbed."""
    rc = run_container()
    if rc != 0:
        return rc

    test_rc = 0
    try:
        test_rc = run_tests(caller=caller, check_bin=check_bin, extra_args=extra_args)
    finally:
        stop_rc = stop_container()
        if test_rc != 0:
            return test_rc
        if stop_rc != 0:
            return stop_rc
    return 0


def clean() -> None:
    """Cleans up ephemeral files and caches."""
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR, ignore_errors=True)
    pytest_cache = FORGE_DIR / ".pytest_cache"
    if pytest_cache.exists():
        shutil.rmtree(pytest_cache, ignore_errors=True)
