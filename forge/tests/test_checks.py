from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from forge.models import SNClientRuntime, TinyproxyRuntime

if TYPE_CHECKING:
    from testinfra.host import Host


class TestSNClientIntegration:
    """End-to-End integration test suite for check_snclient against an Ansible-provisioned testbed."""

    def _exec(self, host: Host, cmd_str: str) -> Any:
        print(f"\n  ▶ Exec : {cmd_str}")
        res = host.run(cmd_str)
        print(f"  ↳ [RC={res.rc}] {res.stdout.strip()}")
        return res

    def test_alive_check(
        self, host: Host, check_bin: str, snclient_info: SNClientRuntime
    ) -> None:
        """Verifies basic HTTPS connectivity to the agent (parent check)."""
        cmd = self._exec(
            host, f"{check_bin} -k -u {snclient_info.url} -p {snclient_info.password}"
        )
        assert cmd.rc == 0
        assert "OK" in cmd.stdout
        assert "REST API reachable" in cmd.stdout

    def test_check_cpu(
        self, host: Host, check_bin: str, snclient_info: SNClientRuntime
    ) -> None:
        """Verifies querying check_cpu against a live agent."""
        cmd = self._exec(
            host,
            f"{check_bin} -k -u {snclient_info.url} -p {snclient_info.password} check_cpu",
        )
        assert cmd.rc in (0, 1)
        assert "total" in cmd.stdout.lower() or "cpu" in cmd.stdout.lower()

    def test_check_uptime(
        self, host: Host, check_bin: str, snclient_info: SNClientRuntime
    ) -> None:
        """Verifies querying check_uptime with perfdata metrics."""
        cmd = self._exec(
            host,
            f"{check_bin} -k -u {snclient_info.url} -p {snclient_info.password} check_uptime",
        )
        assert cmd.rc in (0, 1)
        assert "uptime" in cmd.stdout.lower()

    def test_invalid_password(
        self, host: Host, check_bin: str, snclient_info: SNClientRuntime
    ) -> None:
        """Verifies that bad credentials return non-zero exit code (HTTP 403 Forbidden)."""
        cmd = self._exec(
            host,
            f"{check_bin} -k -u {snclient_info.url} -p bad_password check_cpu",
        )
        assert cmd.rc != 0
        assert "403" in cmd.stdout or "UNKNOWN" in cmd.stdout

    def test_proxy_traversal_success(
        self,
        host: Host,
        check_bin: str,
        snclient_info: SNClientRuntime,
        proxy_info: TinyproxyRuntime,
    ) -> None:
        """Verifies that check_snclient successfully traverses an authentic forward proxy."""
        help_cmd = host.run(f"{check_bin} -h")
        if "-proxy" not in help_cmd.stdout:
            pytest.skip(
                "Provided check_snclient binary does not support the -proxy option"
            )

        cmd = self._exec(
            host,
            f"{check_bin} -k -u {snclient_info.url} -p {snclient_info.password} "
            f"-proxy {proxy_info.url} check_uptime",
        )
        assert cmd.rc in (0, 1)
        assert "uptime" in cmd.stdout.lower()

    def test_proxy_traversal_invalid_auth(
        self,
        host: Host,
        check_bin: str,
        snclient_info: SNClientRuntime,
        proxy_info: TinyproxyRuntime,
    ) -> None:
        """Verifies that invalid proxy credentials fail with exit code 3 (UNKNOWN)."""
        help_cmd = host.run(f"{check_bin} -h")
        if "-proxy" not in help_cmd.stdout:
            pytest.skip(
                "Provided check_snclient binary does not support the -proxy option"
            )

        cmd = self._exec(
            host,
            f"{check_bin} -k -u {snclient_info.url} -p {snclient_info.password} "
            f"-proxy {proxy_info.url_invalid_auth} check_uptime",
        )
        assert cmd.rc == 3
        assert any(
            term in cmd.stdout
            for term in ("Proxy Authentication Required", "Unauthorized", "407")
        )
