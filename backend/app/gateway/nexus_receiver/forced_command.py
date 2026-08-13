"""Console entry point for an sshd ForceCommand receiver account."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from app.gateway.nexus_receiver.release import ReceiverForcedCommandContextProvider
from app.gateway.nexus_receiver.ssh import dispatch_forced_command, forced_command_input_limit


def _read_bounded(stream, limit: int) -> bytes:
    return stream.read(limit + 1)


def _write_stdout(payload: bytes) -> None:
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


async def run_forced_command(
    *,
    principal_id: str | None = None,
    context_provider: ReceiverForcedCommandContextProvider | None = None,
) -> int:
    original_command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    stdin = await asyncio.to_thread(_read_bounded, sys.stdin.buffer, forced_command_input_limit(original_command))
    handler = None
    principal = None
    if principal_id is not None and context_provider is not None:
        try:
            context = await context_provider.get_context(principal_id)
            handler = context.handler
            principal = context.principal
        except Exception:
            # Principal-map and Secret failures are deliberately indistinguishable
            # at the transport boundary and keep the runtime unavailable.
            pass
    result = await dispatch_forced_command(
        original_command,
        stdin,
        handler=handler,
        principal=principal,
    )
    await asyncio.to_thread(_write_stdout, result.stdout)
    return result.exit_code


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--principal-id", required=True)
    args = parser.parse_args()
    # A deployment-owned entry point must inject the context provider. The
    # repository image intentionally has none and therefore remains default-deny.
    raise SystemExit(asyncio.run(run_forced_command(principal_id=args.principal_id)))


if __name__ == "__main__":
    main()
