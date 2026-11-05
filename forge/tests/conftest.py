from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from forge.models import (
    ComponentNotRunningError,
    ComponentRuntime,
    SNClientRuntime,
    TinyproxyRuntime,
)

if TYPE_CHECKING:
    from testinfra.host import Host

FORGE_DIR = Path(__file__).parent.parent.resolve()
REPO_ROOT = FORGE_DIR.parent.resolve()
WORKDIR = FORGE_DIR / "workdir"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--check-bin",
        action="store",
        default=os.environ.get("CHECK_SNCLIENT_BIN"),
        help="Path to check_snclient binary under test (or set CHECK_SNCLIENT_BIN)",
    )


@pytest.fixture(scope="session")
def check_bin(request: pytest.FixtureRequest) -> str:
    candidate = request.config.getoption("--check-bin")
    if candidate:
        p = Path(candidate).resolve()
        if not p.is_file():
            pytest.fail(f"Specified check_snclient binary not found: {p}")
        return str(p)

    repo_bin = REPO_ROOT / "check_snclient"
    if repo_bin.is_file():
        return str(repo_bin)

    pytest.fail(
        f"check_snclient binary not found at '{repo_bin}'. "
        "Please compile it with 'make build' or specify via --check-bin."
    )


@pytest.fixture(scope="session")
def snclient_info() -> SNClientRuntime:
    """Provides SNClient runtime properties and golden path getters."""
    try:
        return SNClientRuntime(runtime_dir=WORKDIR / "snclient" / "runtime")
    except ComponentNotRunningError as err:
        pytest.fail(str(err))


@pytest.fixture(scope="session")
def proxy_info() -> TinyproxyRuntime:
    """Provides Tinyproxy runtime properties and golden path getters."""
    try:
        return TinyproxyRuntime(runtime_dir=WORKDIR / "tinyproxy" / "runtime")
    except ComponentNotRunningError as err:
        pytest.fail(str(err))


@pytest.fixture(scope="module", autouse=True)
def testbed(
    host: Host,
    snclient_info: SNClientRuntime,
    proxy_info: TinyproxyRuntime,
) -> None:
    """Validates that testbed listeners discovered from runtime properties are live."""
    assert host.socket(f"tcp://127.0.0.1:{snclient_info.port}").is_listening, (
        f"SNClient port {snclient_info.port} is not listening"
    )
    assert host.socket(f"tcp://127.0.0.1:{proxy_info.port}").is_listening, (
        f"Tinyproxy port {proxy_info.port} is not listening"
    )
