"""Regression anchor: receiver recovery socket paths stay off the event loop."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.gateway.nexus_receiver.release import (
    ReceiverRecoveryService,
    UnixDatagramReceiverRecoveryNotifier,
)

pytestmark = pytest.mark.asyncio


class _Recoverer:
    def __init__(self) -> None:
        self.calls = 0
        self.called = asyncio.Event()

    async def recover_pending(self) -> list:
        self.calls += 1
        self.called.set()
        return []


async def test_recovery_signal_lifecycle_does_not_block_event_loop() -> None:
    signal_path = Path("/tmp") / f"nexus-receiver-blocking-{uuid4().hex}.sock"
    recoverer = _Recoverer()
    service = ReceiverRecoveryService(
        recoverer,
        poll_interval_seconds=60,
        signal_path=signal_path,
    )
    await service.start()
    recoverer.called.clear()
    notifier = UnixDatagramReceiverRecoveryNotifier(signal_path)
    try:
        notifier.notify_pending()
        await asyncio.wait_for(recoverer.called.wait(), timeout=0.2)
    finally:
        notifier.close()
        await service.stop()

    assert recoverer.calls == 2
    assert not await asyncio.to_thread(signal_path.exists)
