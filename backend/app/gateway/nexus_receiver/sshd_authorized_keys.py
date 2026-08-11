"""Generate restricted authorized_keys from a mounted principal-map Secret."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.gateway.nexus_receiver.release import parse_receiver_principal_map

_FORCED_COMMAND = "/usr/local/bin/nexus-receiver-forced-command"


def render_authorized_keys(payload: bytes) -> bytes:
    principal_map = parse_receiver_principal_map(payload)
    lines = [f'restrict,command="{_FORCED_COMMAND} --principal-id {entry.key_id}" {entry.public_key}' for entry in principal_map.principals]
    return ("\n".join(lines) + "\n").encode()


def write_authorized_keys(source: Path, target: Path) -> None:
    rendered = render_authorized_keys(source.read_bytes())
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
    args = parser.parse_args()
    write_authorized_keys(args.principal_map, args.output)


if __name__ == "__main__":
    main()
