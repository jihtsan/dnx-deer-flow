#!/bin/sh
set -eu

if [ "${NEXUS_RECEIVER_ENABLED:-false}" != "true" ]; then
  echo "Nexus receiver sshd remains disabled" >&2
  exit 78
fi

install -d -m 0755 -o root -g root /run/sshd
install -d -m 0711 -o root -g root /run/nexus-receiver
install -m 0600 -o root -g root /run/secrets/nexus_receiver_host_key /run/nexus-receiver/ssh_host_ed25519_key

cd /app/backend
runtime_environment_args="--sshd-output /run/nexus-receiver/runtime_environment.conf"
runtime_command_args=""
if [ "${NEXUS_RECEIVER_FORCED_COMMAND_PROFILE:-release}" = "acceptance" ]; then
  runtime_environment_args="$runtime_environment_args --postgres-password-file /run/secrets/nexus_receiver_acceptance_postgres_password"
  runtime_command_args="--postgres-password-file /run/secrets/nexus_receiver_acceptance_postgres_password"
fi
# shellcheck disable=SC2086
PYTHONPATH=. uv run --no-sync python -m app.gateway.nexus_receiver.sshd_runtime_environment $runtime_environment_args
# Validate the release gate with the same allowlisted environment that sshd
# injects into each forced-command session.
# shellcheck disable=SC2086
PYTHONPATH=. uv run --no-sync python -m app.gateway.nexus_receiver.sshd_runtime_environment $runtime_command_args -- \
  .venv/bin/python -c \
  'from deerflow.config.app_config import get_app_config; assert get_app_config().nexus_receiver.enabled, "nexus_receiver.enabled is false"'
PYTHONPATH=. uv run --no-sync python -m app.gateway.nexus_receiver.sshd_authorized_keys \
  --principal-map /run/secrets/nexus_receiver_principal_map \
  --output /run/nexus-receiver/authorized_keys \
  --forced-command-profile "${NEXUS_RECEIVER_FORCED_COMMAND_PROFILE:-release}"
chown root:nexus-receiver /run/nexus-receiver/authorized_keys
chmod 0640 /run/nexus-receiver/authorized_keys
grep -q '^restrict,command=' /run/nexus-receiver/authorized_keys

exec /usr/sbin/sshd -D -e -f /etc/ssh/nexus-receiver-sshd_config
