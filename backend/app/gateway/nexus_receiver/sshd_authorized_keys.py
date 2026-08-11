"""Generate restricted authorized_keys from a mounted principal-map Secret."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.gateway.nexus_receiver.release import parse_receiver_principal_map

_FORCED_COMMANDS = {
    "release": "/usr/local/bin/nexus-receiver-forced-command",
    "acceptance": "/usr/local/bin/nexus-receiver-acceptance-forced-command",
}


def render_authorized_keys(payload: bytes, *, forced_command_profile: str = "release") -> bytes:
    try:
        forced_command = _FORCED_COMMANDS[forced_command_profile]
    except KeyError:
        raise ValueError("receiver forced-command profile is invalid") from None
    principal_map = parse_receiver_principal_map(payload)
    lines = [f'restrict,command="{forced_command} --principal-id {entry.key_id}" {entry.public_key}' for entry in principal_map.principals]
    return ("\n".join(lines) + "\n").encode()


def write_authorized_keys(source: Path, target: Path, *, forced_command_profile: str = "release") -> None:
    rendered = render_authorized_keys(source.read_bytes(), forced_command_profile=forced_command_profile)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(rendered)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(target, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--principal-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--forced-command-profile", choices=tuple(_FORCED_COMMANDS), default="release")
    args = parser.parse_args()
    write_authorized_keys(
        args.principal_map,
        args.output,
        forced_command_profile=args.forced_command_profile,
    )


if __name__ == "__main__":
    main()
