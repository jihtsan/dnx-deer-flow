"""Production forced-command entry point using the same composition as Gateway."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.gateway.nexus_receiver.forced_command import run_forced_command
from app.gateway.nexus_receiver.production import ProductionReceiverReleaseBootstrap
from app.gateway.nexus_receiver.release import MappedReceiverForcedCommandContextProvider, UnixDatagramReceiverRecoveryNotifier
from deerflow.config.app_config import get_app_config
from deerflow.persistence.engine import close_engine, init_engine_from_config


async def _build_provider(*, preflight: bool):
    app_config = get_app_config()
    if not app_config.nexus_receiver.production:
        raise ValueError("receiver production profile is disabled")
    await init_engine_from_config(app_config.database)
    bootstrap = ProductionReceiverReleaseBootstrap(
        mounted_host_key_path=Path("/run/secrets/nexus_receiver_host_key") if preflight else None,
        mounted_principal_map_path=Path("/run/secrets/nexus_receiver_principal_map") if preflight else None,
    )
    components = await bootstrap.build(app_config.nexus_receiver)
    signal_path = app_config.nexus_receiver.recovery_signal_socket
    if signal_path is None:
        raise ValueError("receiver recovery signal path is missing")
    notifier = UnixDatagramReceiverRecoveryNotifier(Path(signal_path))
    components.runtime_handler.set_pending_recovery_notifier(notifier.notify_pending)
    return (
        MappedReceiverForcedCommandContextProvider(
            handler=components.runtime_handler,
            principal_mapper=components.principal_mapper,
        ),
        notifier,
    )


async def _run(principal_id: str | None, *, preflight: bool = False) -> int:
    provider = None
    notifier = None
    try:
        provider, notifier = await _build_provider(preflight=preflight)
    except Exception:
        if preflight:
            return 78
    if preflight:
        if notifier is not None:
            notifier.close()
        await close_engine()
        return 0
    try:
        return await run_forced_command(principal_id=principal_id, context_provider=provider)
    finally:
        if notifier is not None:
            notifier.close()
        await close_engine()


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--principal-id")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if not args.preflight and args.principal_id is None:
        parser.error("--principal-id is required")
    raise SystemExit(asyncio.run(_run(args.principal_id, preflight=args.preflight)))


if __name__ == "__main__":
    main()
