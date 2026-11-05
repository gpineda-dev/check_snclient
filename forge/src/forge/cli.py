from __future__ import annotations

from enum import Enum
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Annotated, Optional

from rich.console import Console
from rich.table import Table
import typer

from forge.appliance import ApplianceRegistry, BoundAppliance
from forge.models import (
    ComponentNotRunningError,
    SNClientRuntime,
    TinyproxyRuntime,
)
from forge.orchestrator import (
    BIND_LOCK_FILE,
    CONTAINER_NAME,
    FORGE_DIR,
    REPO_ROOT,
    WORKDIR,
    attach_binary,
    bake_image,
    bind_target,
    clean as orch_clean,
    container_test,
    detach_binary,
    get_bind_info,
    get_slot_info,
    is_bound,
    is_container_running,
    provision,
    run_container,
    run_tests,
    slot_manager,
    stop_container,
    teardown,
    unbind_target,
)
from forge.process import ProcessRunner
from forge.slots import SlotStatus

app = typer.Typer(
    name="forge",
    help="check_snclient E2E Integration Test Forge CLI.\n\n"
    "Provides an isolated, production-grade testbed for check_snclient with live "
    "SNClient agent daemon and Tinyproxy HTTP CONNECT forward proxy services.",
    epilog=(
        "Quick Workflows:\n"
        "  • Local dev loop    : forge up && forge test && forge down\n"
        "  • Single-command run: forge test-all (or forge test-all --backend docker)\n"
        "  • Feature branch    : forge attach /path/to/check_snclient && forge test\n"
        "  • External target   : forge bind staging -u https://remote:8443 -p pass && forge test\n"
    ),
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


class Backend(str, Enum):
    LOCAL = "local"
    DOCKER = "docker"


class Caller(str, Enum):
    AUTO = "auto"
    LOCAL = "local"
    DOCKER = "docker"
    HOST = "host"  # Backward-compatible alias for local


def _get_binary_info(
    candidate_paths: list[Path | str], version_args: list[str]
) -> tuple[str, str, str]:
    """Resolves a binary candidate and extracts its version string."""
    resolved: Path | None = None
    for item in candidate_paths:
        if isinstance(item, Path) and item.is_file() and os.access(item, os.X_OK):
            resolved = item
            break
        elif isinstance(item, str):
            p = ProcessRunner.find_executable(item)
            if p:
                resolved = p
                break

    if not resolved:
        return "[dim]NOT INSTALLED[/dim]", "-", "[dim]Not found in bin/ or PATH[/dim]"

    path_display = str(resolved)
    if resolved.is_symlink():
        path_display = f"{resolved}\n↳ {resolved.readlink()}"

    rc, out, err = ProcessRunner.capture([str(resolved)] + version_args, timeout=2)
    lines = (out or err).strip().splitlines()
    version_str = lines[0] if lines else "Installed"
    status_str = "[green]INSTALLED[/green]" if rc == 0 else "[yellow]DETECTED[/yellow]"
    return status_str, version_str, path_display


@app.command()
def attach(
    target: Annotated[
        str,
        typer.Argument(
            help="Path to custom check_snclient binary to attach to the forge"
        ),
    ],
    component: Annotated[
        str,
        typer.Option(
            "--component", "-c", help="Component slot name in forge/bin/"
        ),
    ] = "check_snclient",
) -> None:
    """Attaches a custom binary to the forge/bin/ slot via symlink.

    If a Docker testbed container is currently running, the binary is automatically
    synchronized and hot-swapped inside the container at /usr/bin/check_snclient.

    Examples:
      forge attach ../dist/check_snclient
      forge attach /tmp/feature-proxy/check_snclient
    """
    try:
        resolved = attach_binary(target, component=component)
        slot = slot_manager.get_slot(component)
        proxy_str = (
            "[bold green]+proxy[/bold green]"
            if slot.has_proxy_support
            else "[dim]no -proxy[/dim]"
        )
        console.print(
            f"[bold green]✔ Attached[/bold green] [bold cyan]{component}[/bold cyan] -> {resolved} ({proxy_str})"
        )
    except Exception as err:
        console.print(f"[bold red]Failed to attach {component}:[/bold red] {err}")
        raise typer.Exit(code=1)


@app.command()
def detach(
    component: Annotated[
        str,
        typer.Option(
            "--component", "-c", help="Component slot name in forge/bin/"
        ),
    ] = "check_snclient",
) -> None:
    """Detaches custom binary slot and resets to default repo build.

    Restores the symlink to ../check_snclient (or unlinks if no repo binary exists).
    If a Docker container is running, synchronizes the default binary back into the container.

    Examples:
      forge detach
    """
    try:
        default_p = detach_binary(component=component)
        if default_p:
            console.print(
                f"[bold green]✔ Detached[/bold green] [bold cyan]{component}[/bold cyan] (reset to default repo binary: {default_p})"
            )
        else:
            console.print(
                f"[bold yellow]✔ Detached[/bold yellow] [bold cyan]{component}[/bold cyan] (slot unlinked)"
            )
    except Exception as err:
        console.print(f"[bold red]Failed to detach {component}:[/bold red] {err}")
        raise typer.Exit(code=1)


@app.command()
def bind(
    name: Annotated[
        str,
        typer.Argument(
            help="Name of the external target environment (e.g. 'staging', 'corp-httpd')"
        ),
    ] = "external",
    file: Annotated[
        Optional[Path],
        typer.Option(
            "--file",
            "-f",
            help="Path to JSON file containing target endpoint definition",
        ),
    ] = None,
    snclient_url: Annotated[
        Optional[str],
        typer.Option(
            "--snclient-url",
            "-u",
            help="SNClient agent base URL (e.g. https://remote-host:8443)",
        ),
    ] = None,
    snclient_pass: Annotated[
        Optional[str],
        typer.Option(
            "--snclient-pass",
            "-p",
            help="SNClient REST API password",
        ),
    ] = None,
    proxy_url: Annotated[
        Optional[str],
        typer.Option(
            "--proxy-url",
            "-P",
            help="Forward proxy URL (e.g. http://proxyuser:proxypass@proxy:3128)",
        ),
    ] = None,
    proxy_invalid_auth: Annotated[
        Optional[str],
        typer.Option(
            "--proxy-invalid-auth",
            help="Proxy URL with invalid credentials (for 407 auth failure testing)",
        ),
    ] = None,
) -> None:
    """Locks the forge to an external pre-existing SNClient and/or Proxy environment.

    Creates a Target Lock (workdir/bind.lock.json) storing endpoint credentials and URLs.
    While locked in bind mode, local and Docker provisioning commands (forge up, forge test-all)
    are safety-blocked to prevent state clobbering.

    Examples:
      forge bind staging -u https://192.168.1.50:8443 -p s3cr3t -P http://proxy:3128
      forge bind --file targets/staging-httpd.json
    """
    if is_bound():
        cur_info = get_bind_info() or {}
        cur_name = cur_info.get("target_name", "external")
        console.print(
            f"[bold red]Error: Forge is already locked to target '{cur_name}'.[/bold red] Run 'forge unbind' first."
        )
        raise typer.Exit(code=1)

    if file:
        fpath = Path(file).resolve()
        if not fpath.is_file():
            console.print(
                f"[bold red]Target profile file not found:[/bold red] {fpath}"
            )
            raise typer.Exit(code=1)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as err:
            console.print(
                f"[bold red]Failed to parse profile JSON:[/bold red] {err}"
            )
            raise typer.Exit(code=1)

        name = data.get("name", name)
        snclient_url = snclient_url or data.get("snclient", {}).get("url")
        snclient_pass = snclient_pass or data.get("snclient", {}).get("password")
        proxy_url = proxy_url or data.get("proxy", {}).get("url")
        proxy_invalid_auth = proxy_invalid_auth or data.get("proxy", {}).get(
            "url_invalid_auth"
        )

    if not snclient_url or not snclient_pass:
        console.print(
            "[bold red]Missing required parameters:[/bold red] --snclient-url and --snclient-pass must be provided (or present in --file)."
        )
        raise typer.Exit(code=1)

    try:
        bind_data = bind_target(
            name=name,
            snclient_url=snclient_url,
            snclient_password=snclient_pass,
            proxy_url=proxy_url,
            proxy_invalid_auth_url=proxy_invalid_auth,
            source_file=str(file) if file else None,
        )
        console.print(
            f"[bold green]✔ Forge LOCKED to external target[/bold green] [bold cyan]'{name}'[/bold cyan]"
        )
        console.print(f"  • SNClient URL : {snclient_url}")
        if proxy_url:
            console.print(f"  • Proxy URL    : {proxy_url}")
        console.print(
            "[dim]Run 'forge status' to check reachability, 'forge test' to run tests, and 'forge unbind' to release lock.[/dim]\n"
        )
        status()
    except Exception as err:
        console.print(f"[bold red]Failed to bind target:[/bold red] {err}")
        raise typer.Exit(code=1)


@app.command()
def unbind() -> None:
    """Releases the external target lock and restores local/docker capabilities.

    Removes workdir/bind.lock.json and cleans bound runtime files.

    Examples:
      forge unbind
    """
    if not is_bound():
        console.print(
            "[dim]Forge is not currently bound to an external target.[/dim]"
        )
        return

    name = unbind_target()
    console.print(
        f"[bold green]✔ Unbound external target[/bold green] [bold cyan]'{name or 'external'}'[/bold cyan]. "
        "Forge is now [bold green]UNLOCKED[/bold green]."
    )


@app.command()
def info(
    check_bin: Annotated[
        Optional[str],
        typer.Option(
            "--check-bin", "-b", help="Path to check_snclient binary under test to inspect"
        ),
    ] = None,
) -> None:
    """Displays useful paths, workdir state, and detected binaries.

    Inspects whether custom binaries are attached, probes for feature support
    (such as the '+proxy' flag), and lists tool versions (SNClient, Tinyproxy, Packer).

    Examples:
      forge info
      forge info --check-bin /path/to/check_snclient
    """
    table = Table(title="Forge Environment & Tooling", header_style="bold magenta")
    table.add_column("Tool / Resource", style="bold")
    table.add_column("Status")
    table.add_column("Version / Features")
    table.add_column("Resolved Path")

    # Paths
    workdir_state = (
        "[green]Active[/green]" if WORKDIR.is_dir() else "[dim]Cleaned / Inactive[/dim]"
    )
    table.add_row("Workdir", "-", workdir_state, str(WORKDIR))

    # Target Mode & Lock state
    if is_bound():
        b_info = get_bind_info() or {}
        target_name = b_info.get("target_name", "external")
        src = b_info.get("source_file") or "CLI"
        table.add_row(
            "Target Environment",
            "[bold yellow]🔒 BOUND (LOCKED)[/bold yellow]",
            f"Target: {target_name} ({src})",
            str(BIND_LOCK_FILE),
        )
    else:
        table.add_row(
            "Target Environment",
            "[green]UNLOCKED[/green]",
            "Local / Docker testbed",
            "Nominal",
        )

    # Target binary resolution (Slot or override)
    if check_bin or os.environ.get("CHECK_SNCLIENT_BIN"):
        target_path = Path(
            check_bin or os.environ.get("CHECK_SNCLIENT_BIN")
        ).resolve()
        status_target, ver_target, path_target = _get_binary_info(
            [target_path], ["-V"]
        )
        if "INSTALLED" in status_target:
            temp_slot = slot_manager.get_slot("check_snclient")
            # Probe proxy on override
            rc, out, err = ProcessRunner.capture([str(target_path), "-h"], timeout=2)
            has_proxy = "-proxy" in (out + err)
            proxy_tag = (
                "[bold green]+proxy[/bold green]"
                if has_proxy
                else "[dim]no -proxy[/dim]"
            )
            ver_target = f"{ver_target} ({proxy_tag})"
        table.add_row(
            "Target Binary (check_snclient)",
            "[yellow]ONE-SHOT OVERRIDE[/yellow]",
            ver_target,
            path_target,
        )
    else:
        slot = slot_manager.get_slot("check_snclient")
        if slot.status == SlotStatus.BROKEN:
            table.add_row(
                "Target Binary (check_snclient)",
                "[bold red]BROKEN LINK[/bold red]",
                "[red]Target file not found[/red]",
                f"{slot.slot_path}\n↳ {slot.target_path}",
            )
        elif slot.status in (SlotStatus.CUSTOM, SlotStatus.DEFAULT, SlotStatus.FILE):
            ver_target = slot.probe_version("-V")
            proxy_tag = (
                "[bold green]+proxy[/bold green]"
                if slot.has_proxy_support
                else "[dim]no -proxy[/dim]"
            )
            ver_target = f"{ver_target} ({proxy_tag})"
            badge = (
                "[bold green]ATTACHED (custom)[/bold green]"
                if slot.is_custom
                else "[cyan]DEFAULT[/cyan]"
            )
            resolved_display = (
                f"{slot.slot_path}\n↳ {slot.resolved_path}"
                if slot.slot_path.is_symlink()
                else str(slot.resolved_path)
            )
            table.add_row(
                "Target Binary (check_snclient)",
                badge,
                ver_target,
                resolved_display,
            )
        else:
            table.add_row(
                "Target Binary (check_snclient)",
                "[dim]NOT ATTACHED[/dim]",
                "-",
                "[dim]No binary found[/dim]",
            )

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
    check_bin: Annotated[
        Optional[str],
        typer.Option(
            "--check-bin", "-b", help="Path to custom check_snclient binary to inspect"
        ),
    ] = None,
) -> None:
    """Inspects the runtime state and socket health of testbed components.

    Checks TCP/HTTP listening sockets for SNClient and Tinyproxy, and prints
    the current Testbed Topology & Placement Matrix.

    Examples:
      forge status
      forge status --all
    """
    if is_bound():
        b_info = get_bind_info() or {}
        target_name = b_info.get("target_name", "external")
        bound_at = b_info.get("bound_at", "-")
        console.print(
            f"[bold yellow]🔒 Testbed LOCKED in BIND mode to target '{target_name}' (bound at {bound_at})[/bold yellow]\n"
        )

    table = Table(title="Testbed Components Status", header_style="bold cyan")
    table.add_column("Component", style="bold")
    table.add_column("State")
    table.add_column("Port")
    table.add_column("Socket Health")
    table.add_column("URL")

    active = ApplianceRegistry.get_active(WORKDIR, CONTAINER_NAME)
    if isinstance(active, BoundAppliance) and active.components:
        for comp in active.components.values():
            table.add_row(*comp.get_status_row())
    else:
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

    # Topology Matrix
    top_table = Table(
        title="Testbed Topology & Placement Matrix", header_style="bold green"
    )
    top_table.add_column("Component", style="bold")
    top_table.add_column("Placement")
    top_table.add_column("Endpoint / Scope")

    bound = is_bound()
    b_info = get_bind_info() if bound else None
    target_name = b_info.get("target_name", "external") if b_info else ""

    container_up = is_container_running()
    sn_placement = (
        f"[bold yellow]external ({target_name})[/bold yellow]"
        if bound
        else (
            "[cyan]docker[/cyan]"
            if container_up
            else (
                "[green]local[/green]"
                if (WORKDIR / "snclient" / "runtime").is_dir()
                else "[dim]inactive[/dim]"
            )
        )
    )
    sn_endpoint = (
        b_info["snclient"]["url"]
        if (bound and b_info and "snclient" in b_info)
        else (f"docker://{CONTAINER_NAME}" if container_up else "local://")
    )

    tp_placement = (
        f"[bold yellow]external ({target_name})[/bold yellow]"
        if (bound and b_info and b_info.get("proxy"))
        else (
            "[dim]none[/dim]"
            if (bound and b_info and not b_info.get("proxy"))
            else (
                "[cyan]docker[/cyan]"
                if container_up
                else (
                    "[green]local[/green]"
                    if (WORKDIR / "tinyproxy" / "runtime").is_dir()
                    else "[dim]inactive[/dim]"
                )
            )
        )
    )
    tp_endpoint = (
        b_info["proxy"]["url"]
        if (bound and b_info and b_info.get("proxy"))
        else (
            "[dim]No proxy bound[/dim]"
            if bound
            else (f"docker://{CONTAINER_NAME}" if container_up else "local://")
        )
    )

    caller_placement = (
        "[green]local[/green]"
        if bound
        else ("[cyan]docker[/cyan]" if container_up else "[green]local[/green]")
    )
    caller_endpoint = (
        "local://"
        if bound
        else (f"docker://{CONTAINER_NAME}" if container_up else "local://")
    )

    top_table.add_row("SNClient Agent", sn_placement, sn_endpoint)
    top_table.add_row("Tinyproxy Proxy", tp_placement, tp_endpoint)
    top_table.add_row(
        "Check Caller",
        f"{caller_placement} (auto)",
        caller_endpoint,
    )

    console.print()
    console.print(top_table)

    if all_info:
        console.print()
        info(check_bin=check_bin)


@app.command()
def up(
    backend: Annotated[
        Backend,
        typer.Option(
            "--backend",
            "-B",
            help="Testbed execution backend ('local' user-space daemons or 'docker' container)",
            case_sensitive=False,
        ),
    ] = Backend.LOCAL,
    snclient_port: Annotated[
        int,
        typer.Option(
            "--snclient-port",
            "-s",
            help="SNClient listening port (local backend only; 0 for dynamic ephemeral port)",
        ),
    ] = 0,
    tinyproxy_port: Annotated[
        int,
        typer.Option(
            "--tinyproxy-port",
            "-p",
            help="Tinyproxy listening port (local backend only; 0 for dynamic ephemeral port)",
        ),
    ] = 0,
) -> None:
    """Provisions and starts testbed daemons (local user-space or Docker container).

    In 'local' mode (default), starts SNClient and Tinyproxy as user-space background
    daemons using dynamic ephemeral ports (or specified static ports).
    In 'docker' mode, starts the 'snclient-testbed' container appliance.

    Examples:
      forge up
      forge up --backend docker
      forge up --snclient-port 18443 --tinyproxy-port 18888
    """
    if is_bound():
        b_info = get_bind_info() or {}
        t_name = b_info.get("target_name", "external")
        console.print(
            f"[bold red]Error: Forge is LOCKED in BIND mode to external target '{t_name}'.[/bold red]\n"
            "Local or Docker testbed provisioning is blocked while bound to an external environment.\n"
            "Run [bold cyan]forge unbind[/bold cyan] to release the lock."
        )
        raise typer.Exit(code=1)

    if backend == Backend.DOCKER:
        console.print(
            "[bold blue]==>[/bold blue] Starting Docker testbed container..."
        )
        rc = run_container()
        if rc != 0:
            console.print(
                "[bold red]Failed to start Docker container testbed![/bold red]"
            )
            raise typer.Exit(code=rc)
    else:
        console.print(
            f"[bold blue]==>[/bold blue] Starting local testbed daemons (snclient={snclient_port}, tinyproxy={tinyproxy_port})..."
        )
        rc = provision(
            snclient_port=snclient_port, tinyproxy_port=tinyproxy_port
        )
        if rc != 0:
            console.print(
                "[bold red]Failed to provision local testbed![/bold red]"
            )
            raise typer.Exit(code=rc)
    status()


@app.command()
def down(
    backend: Annotated[
        Optional[Backend],
        typer.Option(
            "--backend",
            "-B",
            help="Specific backend to stop ('local' or 'docker'). If omitted, stops all active testbeds.",
            case_sensitive=False,
        ),
    ] = None,
    snclient_port: Annotated[
        int,
        typer.Option(
            "--snclient-port", "-s", help="SNClient port for local teardown"
        ),
    ] = 0,
    tinyproxy_port: Annotated[
        int,
        typer.Option(
            "--tinyproxy-port", "-p", help="Tinyproxy port for local teardown"
        ),
    ] = 0,
) -> None:
    """Stops testbed daemons (local and/or Docker) and cleans runtime state.

    Gracefully terminates background daemons, stops Docker containers, and cleans
    ephemeral runtime socket/config directories. If bound, releases the target lock.

    Examples:
      forge down
      forge down --backend docker
    """
    if is_bound():
        t_name = unbind_target()
        console.print(
            f"[bold green]✔ Unbound external target '{t_name or 'external'}'. Lock released.[/bold green]"
        )
        return

    console.print("[bold blue]==>[/bold blue] Stopping testbed daemons...")
    errors = 0

    # Stop Docker container if running or explicitly requested
    if backend in (Backend.DOCKER, None) and is_container_running():
        console.print("  • Stopping Docker container...")
        rc = stop_container()
        if rc != 0:
            errors += 1

    # Stop local daemons if running or explicitly requested
    has_local = (WORKDIR / "snclient" / "runtime").exists() or (
        WORKDIR / "tinyproxy" / "runtime"
    ).exists()
    if (backend == Backend.LOCAL) or (backend is None and has_local):
        console.print("  • Stopping local daemons...")
        rc = teardown(
            snclient_port=snclient_port, tinyproxy_port=tinyproxy_port
        )
        if rc != 0:
            errors += 1

    orch_clean()
    if errors != 0:
        raise typer.Exit(code=1)
    console.print(
        "[bold green]✔ Testbed stopped and runtime cleaned.[/bold green]"
    )


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def test(
    ctx: typer.Context,
    caller: Annotated[
        Caller,
        typer.Option(
            "--caller",
            "-C",
            help="Execution target placement for the check caller ('auto', 'local', or 'docker')",
            case_sensitive=False,
        ),
    ] = Caller.AUTO,
    check_bin: Annotated[
        Optional[str],
        typer.Option(
            "--check-bin",
            "-b",
            help="Path to check_snclient binary under test (defaults to attached slot or repo build)",
        ),
    ] = None,
) -> None:
    """Runs Testinfra integration tests against active testbed.

    Executes integration tests with live command tracing (▶ Exec). Extra arguments
    are forwarded directly to pytest (e.g. -k test_proxy, -x, -vv).

    Examples:
      forge test
      forge test --caller local
      forge test --caller docker
      forge test -k test_proxy_traversal
    """
    resolved_caller = "local" if caller.value == "host" else caller.value
    console.print(
        f"[bold blue]==>[/bold blue] Running Testinfra integration tests (caller: {resolved_caller})..."
    )
    rc = run_tests(
        caller=resolved_caller, check_bin=check_bin, extra_args=ctx.args
    )
    if rc != 0:
        raise typer.Exit(code=rc)


@app.command(
    name="test-all",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def test_all(
    ctx: typer.Context,
    backend: Annotated[
        Backend,
        typer.Option(
            "--backend",
            "-B",
            help="Testbed execution backend ('local' user-space daemons or 'docker' container)",
            case_sensitive=False,
        ),
    ] = Backend.LOCAL,
    caller: Annotated[
        Caller,
        typer.Option(
            "--caller",
            "-C",
            help="Execution target placement for the check caller ('auto', 'local', or 'docker')",
            case_sensitive=False,
        ),
    ] = Caller.AUTO,
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
            help="SNClient listening port (local backend only; 0 for dynamic ephemeral port)",
        ),
    ] = 0,
    tinyproxy_port: Annotated[
        int,
        typer.Option(
            "--tinyproxy-port",
            "-p",
            help="Tinyproxy listening port (local backend only; 0 for dynamic ephemeral port)",
        ),
    ] = 0,
) -> None:
    """Full lifecycle: provisions testbed, runs integration tests, and tears down.

    Ensures a clean, hermetic test cycle. Ideal for CI pipelines and pre-commit validation.

    Examples:
      forge test-all
      forge test-all --backend docker
      forge test-all -k test_proxy
    """
    if is_bound():
        b_info = get_bind_info() or {}
        t_name = b_info.get("target_name", "external")
        console.print(
            f"[bold red]Error: Forge is LOCKED in BIND mode to external target '{t_name}'.[/bold red]\n"
            "Cannot run full provisioning lifecycle on an external environment.\n"
            "Run [bold cyan]forge test[/bold cyan] directly, or [bold cyan]forge unbind[/bold cyan] first."
        )
        raise typer.Exit(code=1)

    console.print(
        f"[bold blue]==>[/bold blue] Running complete test lifecycle (backend: {backend.value}, caller: {caller.value})..."
    )
    if backend == Backend.DOCKER:
        rc = container_test(
            caller=caller.value, check_bin=check_bin, extra_args=ctx.args
        )
        if rc != 0:
            raise typer.Exit(code=rc)
        return

    up_rc = provision(
        snclient_port=snclient_port, tinyproxy_port=tinyproxy_port
    )
    if up_rc != 0:
        console.print("[bold red]Failed to start local testbed![/bold red]")
        raise typer.Exit(code=up_rc)

    test_rc = 0
    try:
        test_rc = run_tests(
            caller=caller.value, check_bin=check_bin, extra_args=ctx.args
        )
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
    """Bakes an immutable OCI container image via Packer.

    Re-uses the canonical Ansible playbook to build 'check_snclient-testbed:latest'.

    Examples:
      forge bake
    """
    console.print("[bold blue]==>[/bold blue] Baking Docker image via Packer...")
    rc = bake_image()
    if rc != 0:
        raise typer.Exit(code=rc)


@app.command()
def clean() -> None:
    """Cleans up ephemeral files and caches.

    Removes workdir/ runtime sockets, .pytest_cache, and temporary files.

    Examples:
      forge clean
    """
    orch_clean()
    console.print(
        "[bold green]Cleaned up temporary directories and caches.[/bold green]"
    )


# --- Backward-compatible hidden aliases ---


@app.command(name="container-run", hidden=True)
def container_run_deprecated() -> None:
    """Deprecated alias for 'forge up --backend docker'."""
    console.print(
        "[dim]Note: 'container-run' is deprecated. Use 'forge up --backend docker' instead.[/dim]"
    )
    up(backend=Backend.DOCKER)


@app.command(name="container-stop", hidden=True)
def container_stop_deprecated() -> None:
    """Deprecated alias for 'forge down --backend docker'."""
    console.print(
        "[dim]Note: 'container-stop' is deprecated. Use 'forge down --backend docker' instead.[/dim]"
    )
    down(backend=Backend.DOCKER)


@app.command(
    name="container-test",
    hidden=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def container_test_deprecated(
    ctx: typer.Context,
    check_bin: Annotated[
        Optional[str], typer.Option("--check-bin", "-b")
    ] = None,
    caller: Annotated[Caller, typer.Option("--caller", "-C")] = Caller.DOCKER,
) -> None:
    """Deprecated alias for 'forge test-all --backend docker'."""
    console.print(
        "[dim]Note: 'container-test' is deprecated. Use 'forge test-all --backend docker' instead.[/dim]"
    )
    test_all(
        ctx=ctx, backend=Backend.DOCKER, caller=caller, check_bin=check_bin
    )


if __name__ == "__main__":
    app()
