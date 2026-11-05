from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Annotated, Optional

from rich.console import Console
from rich.table import Table
import typer

from forge.models import (
    ComponentNotRunningError,
    SNClientRuntime,
    TinyproxyRuntime,
)
from forge.orchestrator import (
    FORGE_DIR,
    REPO_ROOT,
    WORKDIR,
    bake_image,
    clean as orch_clean,
    provision,
    run_container,
    run_tests,
    stop_container,
    teardown,
)

app = typer.Typer(
    name="forge",
    help="check_snclient E2E Integration Test Forge CLI",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _get_binary_info(
    candidate_paths: list[Path | str], version_args: list[str]
) -> tuple[str, str, str]:
    """Resolves a binary candidate and extracts its version string."""
    resolved: str | None = None
    for item in candidate_paths:
        if isinstance(item, Path) and item.is_file() and os.access(item, os.X_OK):
            resolved = str(item)
            break
        elif isinstance(item, str):
            which_p = shutil.which(item)
            if which_p:
                resolved = which_p
                break

    if not resolved:
        return "[dim]NOT INSTALLED[/dim]", "-", "[dim]Not found in bin/ or PATH[/dim]"

    try:
        proc = subprocess.run(
            [resolved] + version_args,
            capture_output=True,
            text=True,
            timeout=2,
        )
        output = (proc.stdout or proc.stderr).strip().splitlines()
        version_str = output[0] if output else "Installed"
        return "[green]INSTALLED[/green]", version_str, resolved
    except Exception as err:
        return "[yellow]DETECTED[/yellow]", f"Error probing version: {err}", resolved


@app.command()
def info() -> None:
    """Displays useful paths, workdir state, and detected binaries."""
    table = Table(title="Forge Environment & Tooling", header_style="bold magenta")
    table.add_column("Tool / Resource", style="bold")
    table.add_column("Status")
    table.add_column("Version / State")
    table.add_column("Resolved Path")

    # Paths
    workdir_state = (
        "[green]Active[/green]" if WORKDIR.is_dir() else "[dim]Cleaned / Inactive[/dim]"
    )
    table.add_row("Workdir", "-", workdir_state, str(WORKDIR))

    repo_bin = REPO_ROOT / "check_snclient"
    status_target, ver_target, path_target = _get_binary_info(
        [repo_bin], ["-V"]
    )
    table.add_row("Target Binary (check_snclient)", status_target, ver_target, path_target)

    status_sn, ver_sn, path_sn = _get_binary_info(
        [FORGE_DIR / "bin" / "snclient", "snclient"], ["--version"]
    )
    table.add_row("SNClient Agent Daemon", status_sn, ver_sn, path_sn)

    status_tp, ver_tp, path_tp = _get_binary_info(
        [FORGE_DIR / "bin" / "tinyproxy", "/usr/bin/tinyproxy", "tinyproxy"], ["-v"]
    )
    table.add_row("Tinyproxy Forward Proxy", status_tp, ver_tp, path_tp)

    status_pk, ver_pk, path_pk = _get_binary_info(
        [FORGE_DIR / "bin" / "packer", "packer"], ["--version"]
    )
    table.add_row("Packer OCI Builder", status_pk, ver_pk, path_pk)

    console.print(table)


@app.command()
def status(
    all_info: Annotated[
        bool,
        typer.Option("--all", "-a", help="Include environment paths and tooling info"),
    ] = False,
) -> None:
    """Inspects the runtime state and socket health of testbed components."""
    table = Table(title="Testbed Components Status", header_style="bold cyan")
    table.add_column("Component", style="bold")
    table.add_column("State")
    table.add_column("Port")
    table.add_column("Socket Health")
    table.add_column("URL")

    # Check SNClient
    sn_dir = WORKDIR / "snclient" / "runtime"
    if sn_dir.is_dir():
        try:
            sn = SNClientRuntime(runtime_dir=sn_dir)
            is_alive = sn.is_listening()
            health_str = (
                "[bold green]LISTENING[/bold green]"
                if is_alive
                else "[bold red]UNREACHABLE[/bold red]"
            )
            state_str = (
                "[green]RUNNING[/green]" if is_alive else "[yellow]STALE[/yellow]"
            )
            table.add_row("SNClient", state_str, str(sn.port), health_str, sn.url)
        except Exception as e:
            table.add_row("SNClient", "[red]ERROR[/red]", "-", "-", str(e))
    else:
        table.add_row(
            "SNClient", "[dim]STOPPED[/dim]", "-", "-", "[dim]Not started[/dim]"
        )

    # Check Tinyproxy
    tp_dir = WORKDIR / "tinyproxy" / "runtime"
    if tp_dir.is_dir():
        try:
            tp = TinyproxyRuntime(runtime_dir=tp_dir)
            is_alive = tp.is_listening()
            health_str = (
                "[bold green]LISTENING[/bold green]"
                if is_alive
                else "[bold red]UNREACHABLE[/bold red]"
            )
            state_str = (
                "[green]RUNNING[/green]" if is_alive else "[yellow]STALE[/yellow]"
            )
            table.add_row("Tinyproxy", state_str, str(tp.port), health_str, tp.url)
        except Exception as e:
            table.add_row("Tinyproxy", "[red]ERROR[/red]", "-", "-", str(e))
    else:
        table.add_row(
            "Tinyproxy", "[dim]STOPPED[/dim]", "-", "-", "[dim]Not started[/dim]"
        )

    console.print(table)

    if all_info:
        console.print()
        info()


@app.command()
def up(
    snclient_port: Annotated[
        int,
        typer.Option(
            "--snclient-port",
            "-s",
            help="SNClient listening port (0 for dynamic ephemeral port)",
        ),
    ] = 0,
    tinyproxy_port: Annotated[
        int,
        typer.Option(
            "--tinyproxy-port",
            "-p",
            help="Tinyproxy listening port (0 for dynamic ephemeral port)",
        ),
    ] = 0,
) -> None:
    """Provisions and starts SNClient and Tinyproxy daemons."""
    console.print(
        f"[bold blue]==>[/bold blue] Starting testbed daemons (snclient={snclient_port}, tinyproxy={tinyproxy_port})..."
    )
    rc = provision(snclient_port=snclient_port, tinyproxy_port=tinyproxy_port)
    if rc != 0:
        console.print("[bold red]Failed to provision testbed![/bold red]")
        raise typer.Exit(code=rc)
    status()


@app.command()
def down(
    snclient_port: Annotated[
        int, typer.Option("--snclient-port", "-s", help="SNClient port")
    ] = 0,
    tinyproxy_port: Annotated[
        int, typer.Option("--tinyproxy-port", "-p", help="Tinyproxy port")
    ] = 0,
) -> None:
    """Stops testbed daemons and cleans runtime metadata."""
    console.print("[bold blue]==>[/bold blue] Stopping testbed daemons...")
    rc = teardown(snclient_port=snclient_port, tinyproxy_port=tinyproxy_port)
    if rc != 0:
        raise typer.Exit(code=rc)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def test(
    ctx: typer.Context,
    check_bin: Annotated[
        Optional[str],
        typer.Option(
            "--check-bin", "-b", help="Path to check_snclient binary under test"
        ),
    ] = None,
) -> None:
    """Runs Testinfra integration tests against active testbed."""
    console.print(
        "[bold blue]==>[/bold blue] Running Testinfra integration tests..."
    )
    rc = run_tests(check_bin=check_bin, extra_args=ctx.args)
    if rc != 0:
        raise typer.Exit(code=rc)


@app.command(name="test-all")
def test_all(
    check_bin: Annotated[
        Optional[str],
        typer.Option(
            "--check-bin", "-b", help="Path to check_snclient binary under test"
        ),
    ] = None,
    snclient_port: Annotated[
        int,
        typer.Option(
            "--snclient-port",
            "-s",
            help="SNClient listening port (0 for dynamic ephemeral port)",
        ),
    ] = 0,
    tinyproxy_port: Annotated[
        int,
        typer.Option(
            "--tinyproxy-port",
            "-p",
            help="Tinyproxy listening port (0 for dynamic ephemeral port)",
        ),
    ] = 0,
) -> None:
    """Full lifecycle: provisions testbed, runs tests, and tears down."""
    console.print(
        "[bold blue]==>[/bold blue] Running complete test lifecycle..."
    )
    up_rc = provision(snclient_port=snclient_port, tinyproxy_port=tinyproxy_port)
    if up_rc != 0:
        console.print("[bold red]Failed to start testbed![/bold red]")
        raise typer.Exit(code=up_rc)

    test_rc = 0
    try:
        test_rc = run_tests(check_bin=check_bin)
    finally:
        down_rc = teardown(
            snclient_port=snclient_port, tinyproxy_port=tinyproxy_port
        )
        if test_rc != 0:
            raise typer.Exit(code=test_rc)
        if down_rc != 0:
            raise typer.Exit(code=down_rc)


@app.command()
def bake() -> None:
    """Bakes an immutable OCI container image via Packer."""
    console.print("[bold blue]==>[/bold blue] Baking Docker image via Packer...")
    rc = bake_image()
    if rc != 0:
        raise typer.Exit(code=rc)


@app.command(name="container-run")
def container_run_cmd() -> None:
    """Runs baked Docker testbed in background."""
    console.print(
        "[bold blue]==>[/bold blue] Starting Docker testbed container..."
    )
    rc = run_container()
    if rc != 0:
        raise typer.Exit(code=rc)


@app.command(name="container-stop")
def container_stop_cmd() -> None:
    """Stops Docker testbed container."""
    console.print(
        "[bold blue]==>[/bold blue] Stopping Docker testbed container..."
    )
    rc = stop_container()
    if rc != 0:
        raise typer.Exit(code=rc)


@app.command()
def clean() -> None:
    """Cleans up ephemeral files and caches."""
    orch_clean()
    console.print(
        "[bold green]Cleaned up temporary directories and caches.[/bold green]"
    )


if __name__ == "__main__":
    app()
