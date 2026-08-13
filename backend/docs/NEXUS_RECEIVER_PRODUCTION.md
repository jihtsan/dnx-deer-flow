# Nexus Skill Receiver Production Assembly

The production profile is an opt-in composition of DeerFlow-owned persistence,
storage, recovery, HTTP, and restricted SSH boundaries with an operator-owned
production provider bundle. It is disabled unless both
`nexus_receiver.enabled: true` and `nexus_receiver.production: true` validate at
startup. Missing or invalid inputs leave HTTP authentication unavailable and
the forced command returns the canonical redacted `RECEIVER_NOT_READY` problem.

The acceptance fixture does not satisfy this production gate and does not count
as production conformance.

## Required External Inputs

The configured `provider_factory` must return
`ProductionReceiverProviderBundle`. The deployment owns these implementations:

- Secret resolver for the cursor key and SSH principal map. Secret bytes must
  never enter `config.yaml`, logs, audit events, or errors.
- Dedicated HTTP service authenticator and stable SSH service-principal map.
  Browser sessions and the generic internal Gateway token are not authority.
- Controlled directory projection and target RBAC. Search output must contain
  only receiver contract fields and bind opaque cursors to principal and query.
- Package trust and compatibility policy. It must verify provenance/signature,
  trust roots, issuer/subject/source, manifest identity, digest, runtime version,
  the authenticated service principal to `actorAudit` binding, and the configured
  policy revisions. DeerFlow repeats the package decision during operation
  recovery before any write; recovery uses the persisted actor binding and does
  not invent a transport principal.
- Durable audit sink and deployment-wide rate limiter. Either provider failing
  keeps the action fail closed; raw credentials, package bytes, directory PII,
  and provider diagnostics must not be recorded.
- Shared-volume probe. It must prove the Gateway sees writable package and
  GLOBAL paths and that the sshd container sees the same durable identities;
  a directory existing only inside one container is insufficient. DeerFlow
  passes `global_writable=true` in Gateway and `false` in the production sshd
  preflight; package staging must be writable in both processes.
- Production readiness probe. It must actively verify the configured directory,
  trust roots/provenance verifier, compatibility matrix, audit destination, and
  deployment-wide rate limiter at their exact policy revisions. An object merely
  exposing the expected methods does not satisfy readiness.

Use PostgreSQL for `database.backend`; production bootstrap rejects memory and
SQLite. Apply migrations through `0014_nexus_receiver_coordination` before
starting either transport. The external PostgreSQL, Secret backend, directory,
trust roots, compatibility matrix, audit destination, and rate-limit backend
must be reviewed production systems rather than test doubles.

## Shared Storage Topology

Layer `docker/docker-compose.nexus-receiver-ssh.yaml` on the production Compose
file. It mounts:

| Volume | Gateway | restricted sshd | Purpose |
| --- | --- | --- | --- |
| `nexus-receiver-global` | read/write | read-only | Native `DEER_FLOW_HOME/integrations/skills/nexus` tree and load probes |
| `nexus-receiver-packages` | read/write | read/write | Durable packages between SSH submit and Gateway recovery |
| `nexus-receiver-ipc` | read/write | read/write | Fixed, authority-free recovery wake datagram |

For multi-node deployments replace the local named volumes with one approved
RWX storage class/PVC mounted at the exact same container paths. PostgreSQL
remains the owner of operation claims, catalog revision, and the singleton
recovery lease. The Gateway recovery leader is the only writer to the final
GLOBAL tree; the one-shot sshd process only stages bytes and SQL state.

## Configuration

```yaml
nexus_receiver:
  enabled: true
  production: true
  ssh_account: nexus-receiver
  recovery_signal_socket: /run/nexus-receiver-ipc/recovery.sock
  provider_factory: enterprise.deerflow_receiver:build_providers
  package_stage_path: /var/lib/deer-flow/nexus-receiver/packages
  global_storage_path: /app/backend/.deer-flow/integrations/skills
  host_key_secret_ref: {name: receiver/ssh, key: host-key}
  principal_map_secret_ref: {name: receiver/ssh, key: principal-map}
  cursor_signing_key_secret_ref: {name: receiver/runtime, key: cursor-key}
  directory_policy_revision: directory-v1
  trust_policy_revision: trust-v1
  compatibility_policy_revision: compatibility-v1
  audit_policy_revision: audit-v1
  rate_limit_policy_revision: rate-v1
```

Set `NEXUS_RECEIVER_ENABLED=true`, `NEXUS_RECEIVER_UV_EXTRAS=postgres`, the two
SSH Secret file paths, `NEXUS_RECEIVER_DATABASE_URL_FILE` containing the same
PostgreSQL DSN used by Gateway, and the normal production PostgreSQL configuration.
The DSN is mounted as a Docker Secret and allowlisted only into forced-command
processes; the sshd container does not inherit the deployment's complete `.env`.
Before opening port 22, sshd also requires the mounted host key and principal map
to byte-match the values returned by the configured Secret resolver. This keeps
the transport and application identity bindings on one Secret revision.
The principal map grants actions explicitly; omit GLOBAL actions until the
external provider bundle and shared-volume probe have passed review.

## Real Conformance

Run conformance only with the production provider bundle and production inputs:

```bash
docker compose \
  -f docker/docker-compose.yaml \
  -f docker/docker-compose.nexus-receiver-ssh.yaml \
  --profile nexus-receiver-ssh config

docker run --rm postgres:17.5 postgres --version

docker compose \
  -f docker/docker-compose.yaml \
  -f docker/docker-compose.nexus-receiver-ssh.yaml \
  --profile nexus-receiver-ssh up --build -d gateway nexus-receiver-sshd

ssh -F ./deployment/nexus-receiver-ssh-config receiver \
  nexus-skill-receiver-v1 capabilities.get < ./conformance/capabilities.json
```

Then execute the canonical positive, negative, idempotency, concurrent GLOBAL,
restart/takeover, exact Observed, directory isolation, rate-limit, audit, and
redaction cases from `contracts/openapi/nexus-skill-receiver-v1.conformance.json`.
Stop and report a blocked production gate if any external input is absent. Do
not substitute `docker-compose.nexus-receiver-acceptance.yaml`, an in-memory
provider, or a fixture result for this run.
