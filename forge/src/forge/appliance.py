from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any

from forge.components import E2EComponent, ProxyComponent, SNClientComponent
from forge.models import (
    SNClientRuntime,
    TinyproxyRuntime,
    check_socket,
)
from forge.process import ProcessRunner


class TestbedAppliance(ABC):
    """Abstract base class representing an execution environment containing E2EComponents."""

    def __init__(self, workdir: Path, name: str = "appliance") -> None:
        self.workdir = workdir
        self.name = name
        self.components: dict[str, E2EComponent] = {}

    def add_component(self, component: E2EComponent) -> None:
        """Registers a modular E2EComponent to the appliance."""
        self.components[component.name] = component

    def get_component(self, name: str) -> E2EComponent | None:
        """Finds a registered component by name."""
        return self.components.get(name)

    @property
    def snclient(self) -> SNClientRuntime | None:
        runtime_dir = self.workdir / "snclient" / "runtime"
        return SNClientRuntime(runtime_dir) if runtime_dir.is_dir() else None

    @property
    def proxy(self) -> TinyproxyRuntime | None:
        runtime_dir = self.workdir / "tinyproxy" / "runtime"
        return TinyproxyRuntime(runtime_dir) if runtime_dir.is_dir() else None

    @abstractmethod
    def start(self) -> None:
        """Starts the testbed daemons or establishes environment state."""

    @abstractmethod
    def stop(self) -> None:
        """Stops daemons and cleans up runtime state."""

    @abstractmethod
    def is_running(self) -> bool:
        """Returns True if the appliance is currently running."""

    def is_healthy(self) -> bool:
        """Checks if all registered components are actively listening."""
        if not self.components:
            sn_ok = self.snclient.is_listening() if self.snclient else False
            tp_ok = self.proxy.is_listening() if self.proxy else True
            return sn_ok and tp_ok
        return all(c.is_listening() for c in self.components.values())

    def sync_binary(self, component: str, binary_path: Path) -> None:
        """Propagates a custom binary to the appliance (default is no-op)."""

    def wait_until_listening(
        self, ports: list[int], host: str = "127.0.0.1", timeout: float = 10.0
    ) -> bool:
        """Polls given TCP ports on host until all are listening or timeout is reached."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if all(check_socket(host, p) for p in ports):
                return True
            time.sleep(0.15)
        return False

    def export_all_runtimes(self) -> None:
        """Exports runtime properties for all registered components."""
        for comp in self.components.values():
            comp.export_runtime(self.workdir)

    def clean_runtime(self) -> None:
        """Removes the ephemeral workdir directory."""
        if self.workdir.exists():
            shutil.rmtree(self.workdir, ignore_errors=True)


class LocalAppliance(TestbedAppliance):
    """Manages local bare-metal daemons in user-space via Ansible playbooks."""

    def __init__(
        self,
        workdir: Path,
        ansible_dir: Path,
        snclient_port: int = 0,
        tinyproxy_port: int = 0,
    ) -> None:
        super().__init__(workdir, name="local")
        self.ansible_dir = ansible_dir
        self.snclient_port = snclient_port
        self.tinyproxy_port = tinyproxy_port

    def _get_ansible_playbook_bin(self) -> str:
        venv_bin = self.workdir.parent / ".venv" / "bin" / "ansible-playbook"
        found = ProcessRunner.find_executable(
            "ansible-playbook", preferred_paths=[venv_bin]
        )
        return str(found) if found else "ansible-playbook"

    def start(self) -> None:
        cmd = [
            self._get_ansible_playbook_bin(),
            "-i",
            "localhost,",
            "-c",
            "local",
            str(self.ansible_dir / "playbook.yml"),
        ]
        env = {
            "SNCLIENT_PORT": str(self.snclient_port),
            "TINYPROXY_PORT": str(self.tinyproxy_port),
        }
        rc = ProcessRunner.run(cmd, cwd=self.ansible_dir.parent, env=env)
        if rc != 0:
            raise RuntimeError(f"Ansible testbed provisioning failed with exit code {rc}")

    def stop(self) -> None:
        cmd = [
            self._get_ansible_playbook_bin(),
            "-i",
            "localhost,",
            "-c",
            "local",
            str(self.ansible_dir / "teardown.yml"),
        ]
        env = {
            "SNCLIENT_PORT": str(self.snclient_port),
            "TINYPROXY_PORT": str(self.tinyproxy_port),
        }
        rc = ProcessRunner.run(cmd, cwd=self.ansible_dir.parent, env=env)
        self.clean_runtime()
        if rc != 0:
            raise RuntimeError(f"Ansible testbed teardown failed with exit code {rc}")

    def is_running(self) -> bool:
        return (self.workdir / "snclient" / "runtime").is_dir()


class DockerAppliance(TestbedAppliance):
    """Manages an immutable OCI container appliance, delegating container mutations to Ansible."""

    def __init__(
        self,
        workdir: Path,
        ansible_dir: Path | None = None,
        container_name: str = "snclient-testbed",
        image_tag: str = "check_snclient-testbed:latest",
        snclient_port: int = 18443,
        tinyproxy_port: int = 18888,
    ) -> None:
        super().__init__(workdir, name="docker")
        self.ansible_dir = ansible_dir or (workdir.parent / "ansible")
        self.container_name = container_name
        self.image_tag = image_tag
        self.snclient_port = snclient_port
        self.tinyproxy_port = tinyproxy_port

    def _get_ansible_playbook_bin(self) -> str:
        venv_bin = self.workdir.parent / ".venv" / "bin" / "ansible-playbook"
        found = ProcessRunner.find_executable(
            "ansible-playbook", preferred_paths=[venv_bin]
        )
        return str(found) if found else "ansible-playbook"

    def is_running(self) -> bool:
        rc, out, _ = ProcessRunner.capture(
            ["docker", "ps", "-q", "-f", f"name={self.container_name}"]
        )
        return rc == 0 and bool(out.strip())

    def start(self) -> None:
        # Stop any previous lingering container instance
        ProcessRunner.capture(["docker", "stop", self.container_name])

        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            self.container_name,
            "-p",
            f"{self.snclient_port}:{self.snclient_port}",
            "-p",
            f"{self.tinyproxy_port}:{self.tinyproxy_port}",
            self.image_tag,
        ]
        rc = ProcessRunner.run(cmd)
        if rc != 0:
            raise RuntimeError(f"Failed to start Docker container '{self.container_name}'")

        # Wait for daemon socket listeners
        if not self.wait_until_listening(
            [self.snclient_port, self.tinyproxy_port], timeout=10.0
        ):
            self.stop()
            raise TimeoutError(
                f"Timed out waiting for container sockets on {self.snclient_port} / {self.tinyproxy_port}"
            )

        # Export runtime metadata into local workdir
        self.clean_runtime()
        (self.workdir / "snclient").mkdir(parents=True, exist_ok=True)
        (self.workdir / "tinyproxy").mkdir(parents=True, exist_ok=True)

        ProcessRunner.capture([
            "docker",
            "cp",
            f"{self.container_name}:/var/run/testbed/snclient/runtime",
            str(self.workdir / "snclient/"),
        ])
        ProcessRunner.capture([
            "docker",
            "cp",
            f"{self.container_name}:/var/run/testbed/tinyproxy/runtime",
            str(self.workdir / "tinyproxy/"),
        ])

    def stop(self) -> None:
        ProcessRunner.capture(["docker", "stop", self.container_name])
        self.clean_runtime()

    def sync_binary(self, component: str, binary_path: Path) -> None:
        """Mutates the container binary slot via Ansible connection: community.docker.docker."""
        if not self.is_running():
            return
        cmd = [
            self._get_ansible_playbook_bin(),
            "-i",
            f"{self.container_name},",
            "-c",
            "community.docker.docker",
            str(self.ansible_dir / "attach.yml"),
            "-e",
            f"target_type=container component={component} binary_src={binary_path}",
        ]
        ProcessRunner.run(cmd, cwd=self.ansible_dir.parent)

    def reset_binary(self, component: str, default_bin: Path | None = None) -> None:
        """Resets the container binary symlink back to nominal build via Ansible."""
        if not self.is_running():
            return
        extra_vars = f"target_type=container component={component}"
        if default_bin and default_bin.is_file():
            extra_vars += f" binary_src={default_bin}"
        cmd = [
            self._get_ansible_playbook_bin(),
            "-i",
            f"{self.container_name},",
            "-c",
            "community.docker.docker",
            str(self.ansible_dir / "detach.yml"),
            "-e",
            extra_vars,
        ]
        ProcessRunner.run(cmd, cwd=self.ansible_dir.parent)


class BoundAppliance(TestbedAppliance):
    """Manages an external target composed of modular E2EComponents with persistent lock protection."""

    def __init__(
        self,
        workdir: Path,
        target_name: str = "external",
        components: list[E2EComponent] | None = None,
        source_file: str | None = None,
    ) -> None:
        super().__init__(workdir, name=target_name)
        self.lock_file = workdir / "bind.lock.json"
        self.source_file = source_file
        if components:
            for comp in components:
                self.add_component(comp)

    @classmethod
    def from_lock_file(cls, workdir: Path) -> BoundAppliance | None:
        lock_file = workdir / "bind.lock.json"
        if not lock_file.is_file():
            return None
        try:
            with open(lock_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            components: list[E2EComponent] = []
            for comp_data in data.get("components", []):
                role = comp_data.get("role")
                if role == "agent":
                    components.append(SNClientComponent.from_dict(comp_data))
                elif role == "proxy":
                    components.append(ProxyComponent.from_dict(comp_data))

            return cls(
                workdir=workdir,
                target_name=data.get("target_name", "external"),
                components=components,
                source_file=data.get("source_file"),
            )
        except Exception:
            return None

    @classmethod
    def is_locked(cls, workdir: Path) -> bool:
        return (workdir / "bind.lock.json").is_file()

    def is_running(self) -> bool:
        return self.lock_file.is_file()

    def start(self) -> None:
        """Establishes target binding, writes component property files, and creates lock."""
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.export_all_runtimes()

        lock_data: dict[str, Any] = {
            "target_name": self.name,
            "bound_at": datetime.now().isoformat(),
            "source_file": self.source_file,
            "components": [c.to_dict() for c in self.components.values()],
        }

        with open(self.lock_file, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, indent=2)

    def stop(self) -> None:
        if self.lock_file.is_file():
            self.lock_file.unlink()
        self.clean_runtime()


class ApplianceRegistry:
    """Factory and active appliance resolver."""

    @staticmethod
    def get_active(
        workdir: Path,
        container_name: str = "snclient-testbed",
    ) -> TestbedAppliance | None:
        """Resolves the currently active testbed appliance."""
        # 1. Target lock mode (highest priority)
        if BoundAppliance.is_locked(workdir):
            return BoundAppliance.from_lock_file(workdir)

        # 2. Docker container mode
        docker_app = DockerAppliance(workdir=workdir, container_name=container_name)
        if docker_app.is_running():
            return docker_app

        # 3. Local bare-metal mode
        local_app = LocalAppliance(workdir=workdir, ansible_dir=workdir.parent / "ansible")
        if local_app.is_running():
            return local_app

        return None
