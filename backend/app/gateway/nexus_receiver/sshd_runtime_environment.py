"""Build the restricted runtime environment used by receiver SSH sessions."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.engine.url import make_url

_PASSTHROUGH_NAMES = (
    "DEER_FLOW_CONFIG_PATH",
    "DEER_FLOW_HOME",
    "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED",
    "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_FAULT_DIR",
    "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_HARNESS_REVISION",
    "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_MANIFEST",
    "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PACKAGE_STAGE",
    "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PREPARATION_REVISION",
    "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PRINCIPAL_MAP_FILE",
    "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PROFILE",
    "DEER_FLOW_PROJECT_ROOT",
    "GATEWAY_WORKERS",
    "NEXUS_RECEIVER_ENABLED",
)


def acceptance_database_url(password: str) -> str:
    if not password or any(character in password for character in "\r\n"):
        raise ValueError("acceptance PostgreSQL password is invalid")
    encoded = quote(password, safe="")
    return f"postgresql://deerflow_acceptance:{encoded}@nexus-receiver-postgres:5432/deerflow_acceptance"


def runtime_environment(source: dict[str, str], *, postgres_password: str | None = None) -> dict[str, str]:
    result = {name: source[name] for name in _PASSTHROUGH_NAMES if name in source}
    if postgres_password is not None:
        result["DATABASE_URL"] = acceptance_database_url(postgres_password)
    return result


def production_database_url(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
        parsed = make_url(value)
    except (OSError, UnicodeError, ValueError):
        raise ValueError("receiver production database Secret is invalid") from None
    if parsed.get_backend_name() != "postgresql" or not parsed.host or not parsed.database or not parsed.username or parsed.password is None:
        raise ValueError("receiver production database Secret is invalid")
    return value


def render_sshd_environment(environment: dict[str, str]) -> str:
    assignments: list[str] = []
    for name, value in sorted(environment.items()):
        if not value or any(character.isspace() for character in value):
            raise ValueError(f"receiver runtime environment value is invalid: {name}")
        assignments.append(f"{name}={value}")
    return f"SetEnv {' '.join(assignments)}\n"


def _read_password(path: Path) -> str:
    return path.read_text(encoding="utf-8").rstrip("\r\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-password-file", type=Path)
    parser.add_argument("--database-url-file", type=Path)
    parser.add_argument("--sshd-output", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    password = _read_password(args.postgres_password_file) if args.postgres_password_file else None
    environment = runtime_environment(dict(os.environ), postgres_password=password)
    if args.database_url_file:
        if password is not None:
            parser.error("acceptance and production database Secrets cannot be combined")
        environment["DATABASE_URL"] = production_database_url(args.database_url_file)
    if args.sshd_output:
        args.sshd_output.write_text(render_sshd_environment(environment), encoding="utf-8")
        os.chmod(args.sshd_output, 0o600)
        if args.command:
            parser.error("a command cannot be combined with --sshd-output")
        return

    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required unless --sshd-output is used")
    os.environ.update(environment)
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
