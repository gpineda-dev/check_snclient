from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from testinfra.host import Host


class TestSNClientIntegration:
    """End-to-End integration test suite for check_snclient against an Ansible-provisioned testbed."""

    def test_alive_check(self, host: Host, check_bin: str, snclient_info: dict[str, str]) -> None:
        """Verifies basic HTTPS connectivity to the agent (parent check)."""
        cmd = host.run(f"{check_bin} -k -u {snclient_info['url']} -p {snclient_info['password']}")
        assert cmd.rc == 0
        assert "OK" in cmd.stdout
        assert "REST API reachable" in cmd.stdout

    def test_check_cpu(self, host: Host, check_bin: str, snclient_info: dict[str, str]) -> None:
        """Verifies querying check_cpu against a live agent."""
        cmd = host.run(f"{check_bin} -k -u {snclient_info['url']} -p {snclient_info['password']} check_cpu")
        assert cmd.rc in (0, 1)
        assert "total" in cmd.stdout.lower() or "cpu" in cmd.stdout.lower()

    def test_check_uptime(self, host: Host, check_bin: str, snclient_info: dict[str, str]) -> None:
        """Verifies querying check_uptime with perfdata metrics."""
        cmd = host.run(f"{check_bin} -k -u {snclient_info['url']} -p {snclient_info['password']} check_uptime")
        assert cmd.rc in (0, 1)
        assert "uptime" in cmd.stdout.lower()

    def test_invalid_password(self, host: Host, check_bin: str, snclient_info: dict[str, str]) -> None:
        """Verifies that bad credentials return non-zero exit code (HTTP 403 Forbidden)."""
        cmd = host.run(f"{check_bin} -k -u {snclient_info['url']} -p bad_password check_cpu")
        assert cmd.rc != 0
        assert "403" in cmd.stdout or "UNKNOWN" in cmd.stdout

    def test_proxy_traversal(
        self,
        host: Host,
        check_bin: str,
        snclient_info: dict[str, str],
        proxy_info: dict[str, str],
    ) -> None:
        """Verifies that check_snclient can traverse an authentic Tinyproxy forward proxy via HTTP CONNECT."""
        help_cmd = host.run(f"{check_bin} -h")
        if "-proxy" not in help_cmd.stdout:
            pytest.skip("Provided check_snclient binary does not support the -proxy option")

        # 1. Valid forward proxy with authentication
        cmd = host.run(
            f"{check_bin} -k -u {snclient_info['url']} -p {snclient_info['password']} "
            f"-proxy {proxy_info['url']} check_uptime"
        )
        assert cmd.rc in (0, 1)
        assert "uptime" in cmd.stdout.lower()

        # 2. Invalid proxy authentication (must fail with exit code 3 UNKNOWN)
        cmd_bad = host.run(
            f"{check_bin} -k -u {snclient_info['url']} -p {snclient_info['password']} "
            f"-proxy {proxy_info['url_invalid_auth']} check_uptime"
        )
        assert cmd_bad.rc == 3
        assert any(term in cmd_bad.stdout for term in ("Proxy Authentication Required", "Unauthorized", "407"))
