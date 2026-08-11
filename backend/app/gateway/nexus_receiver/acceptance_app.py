"""Uvicorn target for the explicitly enabled receiver acceptance profile."""

from app.gateway.app import create_app
from app.gateway.nexus_receiver.acceptance import build_acceptance_bootstrap_from_environment

app = create_app()
app.state.nexus_receiver_release_bootstrap = build_acceptance_bootstrap_from_environment()
