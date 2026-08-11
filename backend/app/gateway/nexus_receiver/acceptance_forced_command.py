"""Acceptance-only forced-command entry point with durable SQL composition."""

from __future__ import annotations

import argparse
import asyncio

from app.gateway.nexus_receiver.acceptance import (
    AcceptanceTransportDisconnect,
    build_acceptance_bootstrap_from_environment,
)
from app.gateway.nexus_receiver.forced_command import run_forced_command
from app.gateway.nexus_receiver.release import (
    MappedReceiverForcedCommandContextProvider,
    UnixDatagramReceiverRecoveryNotifier,
)
from deerflow.config.app_config import get_app_config
from deerflow.persistence.engine import close_engine, init_engine_from_config


async def _run(principal_id: str) -> int:
    provider = None
    notifier = None
    try:
        app_config = get_app_config()
        await init_engine_from_config(app_config.database)
        components = await build_acceptance_bootstrap_from_environment().build(app_config.nexus_receiver)
        signal_path = app_config.nexus_receiver.recovery_signal_socket
        if signal_path is None:
            raise ValueError("acceptance recovery signal path is missing")
        from pathlib import Path

        notifier = UnixDatagramReceiverRecoveryNotifier(Path(signal_path))
        components.runtime_handler.set_pending_recovery_notifier(notifier.notify_pending)
        provider = MappedReceiverForcedCommandContextProvider(
            handler=components.runtime_handler,
            principal_mapper=components.principal_mapper,
        )
    except Exception:
        provider = None

    try:
        return await run_forced_command(principal_id=principal_id, context_provider=provider)
    except AcceptanceTransportDisconnect:
        return 75
    finally:
        if notifier is not None:
            notifier.close()
        await close_engine()


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--principal-id", required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.principal_id)))


if __name__ == "__main__":
    main()
