from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


class ProcessRunner:
    """Encapsulates subprocess execution and binary resolution."""

    @staticmethod
    def run(
        cmd: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        """Executes a command and streams stdout/stderr directly."""
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        proc = subprocess.run(cmd, cwd=cwd, env=merged_env)
        return proc.returncode

    @staticmethod
    def capture(
        cmd: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        """Executes a command and captures its stdout and stderr as text."""
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=merged_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Execution timed out"
        except FileNotFoundError as err:
            return -1, "", str(err)

    @staticmethod
    def find_executable(name: str, preferred_paths: list[Path] | None = None) -> Path | None:
        """Finds an executable in preferred paths or system PATH."""
        if preferred_paths:
            for p in preferred_paths:
                if p.is_file() and os.access(p, os.X_OK):
                    return p
        system_match = shutil.which(name)
        if system_match:
            return Path(system_match)
        return None
