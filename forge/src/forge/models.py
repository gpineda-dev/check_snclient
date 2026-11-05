from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import socket
from typing import Any


class ComponentNotRunningError(RuntimeError):
    """Raised when a component's runtime directory is missing or inactive."""
    pass


def check_socket(host: str, port: int, timeout: float = 0.5) -> bool:
    """Tests if a TCP socket is reachable and accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


@dataclass
class ComponentRuntime:
    """Discovers component properties exposed in configfs-style files under runtime/."""

    runtime_dir: Path
    _data: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.runtime_dir.is_dir():
            component_name = self.runtime_dir.parent.name
            raise ComponentNotRunningError(
                f"{component_name} testbed is not running (missing {self.runtime_dir}). "
                "Please run 'make -C forge up' or 'uv run forge up' first."
            )
        for prop_file in self.runtime_dir.iterdir():
            if prop_file.is_file():
                self._data[prop_file.name] = prop_file.read_text().strip()

    @property
    def is_ready(self) -> bool:
        """Returns True if the runtime directory exists and has properties loaded."""
        return bool(self._data)

    def is_listening(self, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
        """Checks if the component's port is listening on the host."""
        if "port" not in self._data:
            return False
        return check_socket(host, int(self._data["port"]), timeout=timeout)

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
