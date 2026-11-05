from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from forge.models import check_socket


class E2EComponent(ABC):
    """Abstract contract for an isolated testbed component (agent, proxy, service)."""

    name: str
    role: str

    @abstractmethod
    def is_listening(self, timeout: float = 0.5) -> bool:
        """Tests whether the component's primary network endpoint is reachable."""

    @abstractmethod
    def export_runtime(self, workdir: Path) -> None:
        """Publishes component runtime properties into the flat file state contract."""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Serializes component state for persistence in lock files."""

    @abstractmethod
    def get_status_row(self) -> tuple[str, str, str, str, str]:
        """Returns (Component, State, Port, Socket Health, URL) for status display."""


@dataclass
class SNClientComponent(E2EComponent):
    """SNClient agent component contract."""

    url: str
    password: str
    port: int = 0
    name: str = "SNClient"
    role: str = "agent"

    def __post_init__(self) -> None:
        if not self.port:
            parsed = urlparse(self.url)
            self.port = parsed.port or (443 if parsed.scheme == "https" else 80)

    @property
    def host(self) -> str:
        parsed = urlparse(self.url)
        return parsed.hostname or "127.0.0.1"

    def is_listening(self, timeout: float = 0.5) -> bool:
        return check_socket(self.host, self.port, timeout=timeout)

    def export_runtime(self, workdir: Path) -> None:
        sn_dir = workdir / "snclient" / "runtime"
        sn_dir.mkdir(parents=True, exist_ok=True)
        (sn_dir / "url").write_text(self.url)
        (sn_dir / "port").write_text(str(self.port))
        (sn_dir / "password").write_text(self.password)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "url": self.url,
            "port": self.port,
            "password": self.password,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SNClientComponent:
        return cls(
            name=data.get("name", "SNClient"),
            role=data.get("role", "agent"),
            url=data["url"],
            port=data.get("port", 0),
            password=data["password"],
        )

    def get_status_row(self) -> tuple[str, str, str, str, str]:
        alive = self.is_listening()
        health = "[bold green]LISTENING[/bold green]" if alive else "[bold red]UNREACHABLE[/bold red]"
        state = "[green]RUNNING[/green]" if alive else "[yellow]STALE[/yellow]"
        return self.name, state, str(self.port), health, self.url


@dataclass
class ProxyComponent(E2EComponent):
    """HTTP/HTTPS forward proxy component contract (Tinyproxy, Squid, Apache)."""

    url: str
    port: int = 0
    user: str | None = None
    password: str | None = None
    url_invalid_auth: str | None = None
    name: str = "Tinyproxy"
    role: str = "proxy"

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if not self.port:
            self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.username and not self.user:
            self.user = parsed.username
        if parsed.password and not self.password:
            self.password = parsed.password
        if not self.url_invalid_auth:
            host_part = parsed.netloc.split("@")[-1]
            self.url_invalid_auth = f"{parsed.scheme}://wronguser:wrongpass@{host_part}"

    @property
    def host(self) -> str:
        parsed = urlparse(self.url)
        return parsed.hostname or "127.0.0.1"

    def is_listening(self, timeout: float = 0.5) -> bool:
        return check_socket(self.host, self.port, timeout=timeout)

    def export_runtime(self, workdir: Path) -> None:
        tp_dir = workdir / "tinyproxy" / "runtime"
        tp_dir.mkdir(parents=True, exist_ok=True)
        (tp_dir / "url").write_text(self.url)
        (tp_dir / "port").write_text(str(self.port))
        if self.user:
            (tp_dir / "user").write_text(self.user)
        if self.password:
            (tp_dir / "password").write_text(self.password)
        if self.url_invalid_auth:
            (tp_dir / "url_invalid_auth").write_text(self.url_invalid_auth)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "url": self.url,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "url_invalid_auth": self.url_invalid_auth,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProxyComponent:
        return cls(
            name=data.get("name", "Tinyproxy"),
            role=data.get("role", "proxy"),
            url=data["url"],
            port=data.get("port", 0),
            user=data.get("user"),
            password=data.get("password"),
            url_invalid_auth=data.get("url_invalid_auth"),
        )

    def get_status_row(self) -> tuple[str, str, str, str, str]:
        alive = self.is_listening()
        health = "[bold green]LISTENING[/bold green]" if alive else "[bold red]UNREACHABLE[/bold red]"
        state = "[green]RUNNING[/green]" if alive else "[yellow]STALE[/yellow]"
        return self.name, state, str(self.port), health, self.url
