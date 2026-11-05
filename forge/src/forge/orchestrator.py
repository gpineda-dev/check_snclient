from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


FORGE_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = FORGE_DIR.parent
WORKDIR = FORGE_DIR / "workdir"
ANSIBLE_DIR = FORGE_DIR / "ansible"
PACKER_BIN = FORGE_DIR / "bin" / "packer"
PACKER_TEMPLATE = FORGE_DIR / "packer" / "testbed.pkr.hcl"

IMAGE_TAG = "check_snclient-testbed:latest"
CONTAINER_NAME = "snclient-testbed"


def _run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Executes a subprocess and streams output to stdout/stderr in real time."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    proc = subprocess.run(cmd, cwd=cwd or FORGE_DIR, env=merged_env)
    return proc.returncode


def get_ansible_playbook_bin() -> str:
    """Locates ansible-playbook within the virtualenv or PATH."""
    venv_bin = FORGE_DIR / ".venv" / "bin" / "ansible-playbook"
    if venv_bin.is_file() and os.access(venv_bin, os.X_OK):
        return str(venv_bin)
    candidate = shutil.which("ansible-playbook")
    if candidate:
        return candidate
    return sys.executable.replace("python", "ansible-playbook")


def get_pytest_bin() -> str:
    """Locates pytest within the virtualenv or PATH."""
    venv_bin = FORGE_DIR / ".venv" / "bin" / "pytest"
    if venv_bin.is_file() and os.access(venv_bin, os.X_OK):
        return str(venv_bin)
    candidate = shutil.which("pytest")
    if candidate:
        return candidate
    return sys.executable.replace("python", "pytest")


def provision(snclient_port: int = 0, tinyproxy_port: int = 0) -> int:
    """Runs the Ansible testbed provisioning playbook."""
    cmd = [
        get_ansible_playbook_bin(),
        "-i",
        "localhost,",
        "-c",
        "local",
        str(ANSIBLE_DIR / "playbook.yml"),
    ]
    env = {
        "SNCLIENT_PORT": str(snclient_port),
        "TINYPROXY_PORT": str(tinyproxy_port),
    }
    return _run_cmd(cmd, env=env)


def teardown(snclient_port: int = 0, tinyproxy_port: int = 0) -> int:
    """Runs the Ansible testbed teardown playbook."""
    cmd = [
        get_ansible_playbook_bin(),
        "-i",
        "localhost,",
        "-c",
        "local",
        str(ANSIBLE_DIR / "teardown.yml"),
    ]
    env = {
        "SNCLIENT_PORT": str(snclient_port),
        "TINYPROXY_PORT": str(tinyproxy_port),
    }
    return _run_cmd(cmd, env=env)


def run_tests(
    check_bin: str | None = None,
    extra_args: list[str] | None = None,
) -> int:
    """Executes pytest-testinfra against the active testbed."""
    cmd = [get_pytest_bin(), "tests"]
    if check_bin:
        cmd.extend(["--check-bin", check_bin])
    if extra_args:
        cmd.extend(extra_args)

    env = {}
    if check_bin:
        env["CHECK_SNCLIENT_BIN"] = check_bin
    return _run_cmd(cmd, env=env)


def bake_image() -> int:
    """Builds the standalone Docker testbed image via Packer."""
    if not PACKER_BIN.is_file() or not os.access(PACKER_BIN, os.X_OK):
        raise RuntimeError(
            f"Packer binary not found at {PACKER_BIN}. Run 'make packer-init' first."
        )
    return _run_cmd([str(PACKER_BIN), "build", str(PACKER_TEMPLATE)])


def run_container() -> int:
    """Runs the baked Docker container in the background."""
    cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        CONTAINER_NAME,
        "-p",
        "18443:18443",
        "-p",
        "18888:18888",
        IMAGE_TAG,
    ]
    return _run_cmd(cmd)


def stop_container() -> int:
    """Stops the running Docker container."""
    cmd = ["docker", "stop", CONTAINER_NAME]
    return _run_cmd(cmd)


def clean() -> None:
    """Cleans up ephemeral files and caches."""
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR, ignore_errors=True)
    pytest_cache = FORGE_DIR / ".pytest_cache"
    if pytest_cache.exists():
        shutil.rmtree(pytest_cache, ignore_errors=True)
