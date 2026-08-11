"""Default-deny provider framework for the DeerFlow-owned Nexus receiver."""

from fastapi import FastAPI

from app.gateway.nexus_receiver.auth import ReceiverProviderError
from app.gateway.nexus_receiver.router import receiver_provider_error_handler, router


def install_nexus_receiver_provider(app: FastAPI) -> None:
    """Mount the read-only receiver surface and its stable error handler."""
    app.add_exception_handler(ReceiverProviderError, receiver_provider_error_handler)
    app.include_router(router)


__all__ = ["install_nexus_receiver_provider"]
