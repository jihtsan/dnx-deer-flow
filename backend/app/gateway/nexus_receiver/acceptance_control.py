"""Local-only CLI for arming deterministic acceptance faults."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import cast

from app.gateway.nexus_receiver.acceptance import (
    AcceptanceFault,
    AcceptanceFaultController,
    load_acceptance_settings,
)

_FAULTS = (
    "disconnect_after_receiver_accept",
    "restart_nexus_after_outcome_unknown",
    "restart_deer_flow_before_activation",
    "fail_activation_after_atomic_install",
    "disable_after_success",
    "observation_unavailable",
)


async def _run(action: str, fault: str) -> None:
    controller = AcceptanceFaultController(load_acceptance_settings().fault_path)
    accepted_fault = cast(AcceptanceFault, fault)
    if action == "arm":
        await controller.arm(accepted_fault)
    elif action == "clear":
        await controller.clear(accepted_fault)
    else:
        state = "armed" if await controller.is_armed(accepted_fault) else "not_armed"
        print(json.dumps({"fault": fault, "state": state}, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("arm", "clear", "status"))
    parser.add_argument("fault", choices=_FAULTS)
    args = parser.parse_args()
    asyncio.run(_run(args.action, args.fault))


if __name__ == "__main__":
    main()
