"""Console entry point for an sshd ForceCommand receiver account."""

from __future__ import annotations

import asyncio
import os
import sys

from app.gateway.nexus_receiver.ssh import dispatch_forced_command, forced_command_input_limit


def _read_bounded(stream, limit: int) -> bytes:
    return stream.read(limit + 1)


def _write_stdout(payload: bytes) -> None:
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


async def _run() -> int:
    original_command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    stdin = await asyncio.to_thread(_read_bounded, sys.stdin.buffer, forced_command_input_limit(original_command))
    # Release wiring is intentionally absent: host-key, service-auth, directory,
    # Secret and policy gates must inject an approved runtime in a later slice.
    result = await dispatch_forced_command(original_command, stdin, handler=None)
    await asyncio.to_thread(_write_stdout, result.stdout)
    return result.exit_code


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
