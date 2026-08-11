from __future__ import annotations

from pathlib import Path

import yaml

from app.gateway.nexus_receiver.sshd_authorized_keys import render_authorized_keys

ROOT = Path(__file__).resolve().parents[2]


def test_receiver_sshd_is_opt_in_and_loopback_bound() -> None:
    overlay = yaml.safe_load((ROOT / "docker" / "docker-compose.nexus-receiver-ssh.yaml").read_text(encoding="utf-8"))
    service = overlay["services"]["nexus-receiver-sshd"]

    assert service["profiles"] == ["nexus-receiver-ssh"]
    assert service["ports"] == ["${NEXUS_RECEIVER_SSH_BIND_HOST:-127.0.0.1}:${NEXUS_RECEIVER_SSH_PORT:-2222}:22"]
    assert service["build"]["target"] == "nexus-receiver-sshd"
    assert service["secrets"] == ["nexus_receiver_host_key", "nexus_receiver_principal_map"]


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


def test_receiver_authorized_keys_generator_uses_exact_restrictions() -> None:
    entrypoint = (ROOT / "docker" / "nexus-receiver-sshd" / "entrypoint.sh").read_text(encoding="utf-8")
    rendered = render_authorized_keys(b'{"version":1,"principals":[{"keyId":"release-key-1","subject":"nexus.release","actions":["receiver:capabilities:read"],"publicKey":"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIReleaseKey nexus"}]}').decode()

    assert "restrict,command=" in entrypoint
    assert rendered.startswith('restrict,command="/usr/local/bin/nexus-receiver-forced-command --principal-id release-key-1" ssh-ed25519 ')
    assert rendered.endswith(" nexus\n")
    assert "nexus_receiver_host_key" in entrypoint
    assert "nexus_receiver_principal_map" in entrypoint
