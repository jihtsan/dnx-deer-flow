from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.gateway.nexus_receiver.sshd_authorized_keys import render_authorized_keys
from app.gateway.nexus_receiver.sshd_runtime_environment import (
    acceptance_database_url,
    production_database_url,
    render_sshd_environment,
    runtime_environment,
)

ROOT = Path(__file__).resolve().parents[2]


def test_receiver_sshd_is_opt_in_and_loopback_bound() -> None:
    overlay = yaml.safe_load((ROOT / "docker" / "docker-compose.nexus-receiver-ssh.yaml").read_text(encoding="utf-8"))
    service = overlay["services"]["nexus-receiver-sshd"]

    assert service["profiles"] == ["nexus-receiver-ssh"]
    assert service["ports"] == ["${NEXUS_RECEIVER_SSH_BIND_HOST:-127.0.0.1}:${NEXUS_RECEIVER_SSH_PORT:-2222}:22"]
    assert service["build"]["target"] == "nexus-receiver-sshd"
    assert service["build"]["args"]["UV_EXTRAS"] == "${NEXUS_RECEIVER_UV_EXTRAS:-}"
    assert service["secrets"] == ["nexus_receiver_host_key", "nexus_receiver_principal_map", "nexus_receiver_database_url"]
    assert "nexus-receiver-ipc:/run/nexus-receiver-ipc" in service["volumes"]
    assert "nexus-receiver-ipc:/run/nexus-receiver-ipc" in overlay["services"]["gateway"]["volumes"]
    assert "nexus-receiver-global:/app/backend/.deer-flow/integrations/skills:ro" in service["volumes"]
    assert "nexus-receiver-global:/app/backend/.deer-flow/integrations/skills" in overlay["services"]["gateway"]["volumes"]
    assert "nexus-receiver-packages:/var/lib/deer-flow/nexus-receiver/packages" in service["volumes"]
    assert "nexus-receiver-packages:/var/lib/deer-flow/nexus-receiver/packages" in overlay["services"]["gateway"]["volumes"]
    assert service["environment"]["NEXUS_RECEIVER_FORCED_COMMAND_PROFILE"] == "production"


def test_receiver_sshd_forbids_interactive_and_forwarding_surfaces() -> None:
    config = (ROOT / "docker" / "nexus-receiver-sshd" / "sshd_config").read_text(encoding="utf-8")

    for directive in (
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "PermitRootLogin no",
        "PermitTTY no",
        "AllowTcpForwarding no",
        "AllowAgentForwarding no",
        "X11Forwarding no",
        "PermitTunnel no",
    ):
        assert directive in config
    assert "Subsystem sftp" not in config
    assert "AllowUsers nexus-receiver" in config


def test_receiver_account_allows_public_key_auth_but_has_no_password() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "passwd -l nexus-receiver" not in dockerfile
    assert "usermod --password 'x' nexus-receiver" in dockerfile
    assert "PasswordAuthentication no" in (ROOT / "docker" / "nexus-receiver-sshd" / "sshd_config").read_text(encoding="utf-8")


def test_receiver_authorized_keys_generator_uses_exact_restrictions() -> None:
    entrypoint = (ROOT / "docker" / "nexus-receiver-sshd" / "entrypoint.sh").read_text(encoding="utf-8")
    rendered = render_authorized_keys(b'{"version":1,"principals":[{"keyId":"release-key-1","subject":"nexus.release","actions":["receiver:capabilities:read"],"publicKey":"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIReleaseKey nexus"}]}').decode()

    assert "restrict,command=" in entrypoint
    assert rendered.startswith('restrict,command="/usr/local/bin/nexus-receiver-forced-command --principal-id release-key-1" ssh-ed25519 ')
    assert rendered.endswith(" nexus\n")
    assert "nexus_receiver_host_key" in entrypoint
    assert "nexus_receiver_principal_map" in entrypoint


def test_receiver_image_includes_only_the_required_sshd_build_context() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "docker/*" in dockerignore
    assert "!docker/nexus-receiver-sshd/**" in dockerignore
    assert "\ndocker/\n" not in dockerignore


def test_acceptance_images_include_postgres_and_share_the_password_secret() -> None:
    overlay = yaml.safe_load((ROOT / "docker" / "docker-compose.nexus-receiver-acceptance.yaml").read_text(encoding="utf-8"))

    for service_name in ("gateway", "nexus-receiver-sshd"):
        service = overlay["services"][service_name]
        assert service["build"]["args"]["UV_EXTRAS"] == "postgres"
        assert "nexus_receiver_acceptance_postgres_password" in service["secrets"]


def test_sshd_runtime_environment_is_allowlisted_and_builds_database_url() -> None:
    environment = runtime_environment(
        {
            "DEER_FLOW_CONFIG_PATH": "/app/backend/config.yaml",
            "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED": "true",
            "UNRELATED_SECRET": "must-not-pass",
        },
        postgres_password="p@ss word",
    )

    assert environment["DATABASE_URL"] == acceptance_database_url("p@ss word")
    assert environment["DATABASE_URL"] == ("postgresql://deerflow_acceptance:p%40ss%20word@nexus-receiver-postgres:5432/deerflow_acceptance")
    assert "UNRELATED_SECRET" not in environment
    rendered = render_sshd_environment(environment)
    assert rendered.count("SetEnv ") == 1
    assert "DEER_FLOW_CONFIG_PATH=/app/backend/config.yaml" in rendered
    assert "DATABASE_URL=postgresql://" in rendered


def test_production_database_url_is_loaded_only_from_a_postgres_secret(tmp_path: Path) -> None:
    secret = tmp_path / "database-url"
    secret.write_text("postgresql://receiver:password@postgres:5432/deerflow\n", encoding="utf-8")

    assert production_database_url(secret) == "postgresql://receiver:password@postgres:5432/deerflow"

    secret.write_text("sqlite:///receiver.db", encoding="utf-8")
    with pytest.raises(ValueError, match="database Secret is invalid"):
        production_database_url(secret)


def test_sshd_entrypoint_creates_runtime_directory_and_readable_authorized_keys() -> None:
    entrypoint = (ROOT / "docker" / "nexus-receiver-sshd" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "install -d -m 0755 -o root -g root /run/sshd" in entrypoint
    assert "install -d -m 0711 -o root -g root /run/nexus-receiver" in entrypoint
    assert "install -d -m 0700 -o nexus-receiver -g nexus-receiver /var/lib/deer-flow/nexus-receiver/packages" in entrypoint
    assert "chown root:nexus-receiver /run/nexus-receiver/authorized_keys" in entrypoint
    assert "chmod 0640 /run/nexus-receiver/authorized_keys" in entrypoint
    assert entrypoint.index("sshd_runtime_environment $runtime_environment_args") < entrypoint.index("get_app_config")
    assert "sshd_runtime_environment $runtime_command_args --" in entrypoint
    assert "production_forced_command --preflight" in entrypoint
    assert "Include /run/nexus-receiver/runtime_environment.conf" in (ROOT / "docker" / "nexus-receiver-sshd" / "sshd_config").read_text(encoding="utf-8")
