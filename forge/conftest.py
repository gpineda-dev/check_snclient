from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from testinfra.host import Host

FORGE_DIR = Path(__file__).parent.resolve()
REPO_ROOT = FORGE_DIR.parent.resolve()
WORKDIR = FORGE_DIR / "workdir"


@dataclass
class ComponentRuntime:
    """Discovers component properties exposed in configfs-style files under runtime/."""

    runtime_dir: Path
    _data: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.runtime_dir.is_dir():
            component_name = self.runtime_dir.parent.name
            pytest.fail(
                f"{component_name} testbed is not running (missing {self.runtime_dir}). "
                "Please run 'make -C forge up' first."
            )
        for prop_file in self.runtime_dir.iterdir():
            if prop_file.is_file():
                self._data[prop_file.name] = prop_file.read_text().strip()

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> str:
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data


@dataclass
class SNClientRuntime(ComponentRuntime):
    """Strongly-typed golden path accessors for SNClient agent properties."""

    @property
    def port(self) -> int:
        return int(self._data["port"])

    @property
    def url(self) -> str:
        return self._data["url"]

    @property
    def password(self) -> str:
        return self._data["password"]


@dataclass
class TinyproxyRuntime(ComponentRuntime):
    """Strongly-typed golden path accessors for Tinyproxy forward proxy properties."""

    @property
    def port(self) -> int:
        return int(self._data["port"])

    @property
    def url(self) -> str:
        return self._data["url"]

    @property
    def url_invalid_auth(self) -> str:
        return self._data["url_invalid_auth"]

    @property
    def user(self) -> str:
        return self._data["user"]

    @property
    def password(self) -> str:
        return self._data["password"]


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
    return SNClientRuntime(runtime_dir=WORKDIR / "snclient" / "runtime")


@pytest.fixture(scope="session")
def proxy_info() -> TinyproxyRuntime:
    """Provides Tinyproxy runtime properties and golden path getters."""
    return TinyproxyRuntime(runtime_dir=WORKDIR / "tinyproxy" / "runtime")


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
