from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Any

from forge.process import ProcessRunner


class SlotStatus(str, Enum):
    DEFAULT = "default"
    CUSTOM = "custom"
    FILE = "file"
    BROKEN = "broken"
    UNLINKED = "unlinked"


@dataclass
class BinarySlot:
    """Represents a component binary slot in forge/bin/."""

    name: str
    slot_path: Path
    target_path: Path | None
    resolved_path: Path | None
    status: SlotStatus

    @property
    def is_custom(self) -> bool:
        return self.status in (SlotStatus.CUSTOM, SlotStatus.FILE)

    @property
    def is_valid(self) -> bool:
        return (
            self.resolved_path is not None
            and self.resolved_path.is_file()
            and os.access(self.resolved_path, os.X_OK)
        )

    @property
    def has_proxy_support(self) -> bool:
        """Probes whether the binary supports the -proxy argument in its help text."""
        if not self.is_valid:
            return False
        rc, out, err = ProcessRunner.capture(
            [str(self.resolved_path), "-h"], timeout=2
        )
        return "-proxy" in (out + err)

    def probe_version(self, flag: str = "-V") -> str:
        """Extracts the binary version string."""
        if not self.is_valid:
            return "Not available"
        rc, out, err = ProcessRunner.capture(
            [str(self.resolved_path), flag], timeout=2
        )
        lines = (out or err).strip().splitlines()
        return lines[0] if lines else "Installed"


class SlotManager:
    """Manages hot-swappable component binary slots under forge/bin/."""

    def __init__(self, bin_dir: Path, repo_root: Path) -> None:
        self.bin_dir = bin_dir
        self.repo_root = repo_root

    def attach(self, target_path: Path | str, component: str = "check_snclient") -> BinarySlot:
        """Attaches an external executable binary to the slot via symlink."""
        src = Path(target_path).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Target binary not found: {src}")
        if not os.access(src, os.X_OK):
            raise PermissionError(f"Target binary is not executable: {src}")

        self.bin_dir.mkdir(parents=True, exist_ok=True)
        slot = self.bin_dir / component
        if slot.is_symlink() or slot.exists():
            slot.unlink()
        slot.symlink_to(src)

        return self.get_slot(component)

    def detach(self, component: str = "check_snclient") -> BinarySlot:
        """Detaches any custom binary slot and resets to default repository build."""
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        slot = self.bin_dir / component
        if slot.is_symlink() or slot.exists():
            slot.unlink()

        default_bin = self.repo_root / component
        if default_bin.is_file():
            slot.symlink_to(default_bin)

        return self.get_slot(component)

    def get_slot(self, component: str = "check_snclient") -> BinarySlot:
        """Inspects and returns the current state of a binary slot."""
        slot = self.bin_dir / component
        default_bin = self.repo_root / component

        if slot.is_symlink():
            raw_target = slot.readlink()
            resolved = slot.resolve()
            if not resolved.is_file():
                return BinarySlot(
                    name=component,
                    slot_path=slot,
                    target_path=raw_target,
                    resolved_path=resolved,
                    status=SlotStatus.BROKEN,
                )
            is_default = (
                (resolved == default_bin.resolve())
                if default_bin.exists()
                else False
            )
            return BinarySlot(
                name=component,
                slot_path=slot,
                target_path=raw_target,
                resolved_path=resolved,
                status=SlotStatus.DEFAULT if is_default else SlotStatus.CUSTOM,
            )

        if slot.is_file():
            return BinarySlot(
                name=component,
                slot_path=slot,
                target_path=slot,
                resolved_path=slot,
                status=SlotStatus.FILE,
            )

        if default_bin.is_file():
            try:
                self.bin_dir.mkdir(parents=True, exist_ok=True)
                slot.symlink_to(default_bin)
                return BinarySlot(
                    name=component,
                    slot_path=slot,
                    target_path=default_bin,
                    resolved_path=default_bin,
                    status=SlotStatus.DEFAULT,
                )
            except OSError:
                pass

        return BinarySlot(
            name=component,
            slot_path=slot,
            target_path=None,
            resolved_path=default_bin if default_bin.is_file() else None,
            status=SlotStatus.UNLINKED,
        )
