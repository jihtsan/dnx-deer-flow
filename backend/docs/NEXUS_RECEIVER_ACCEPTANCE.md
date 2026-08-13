# Nexus Receiver USER Acceptance Harness

This harness is the Deer Flow half of the Skill Hub P0 USER joint-acceptance
preparation. It is stacked on Deer Flow PR #18 at `e2fa130f` and aligned with
the preparation manifest from Nexus PR #29 at `ad8600e3`.

It is not a production receiver bootstrap. The ordinary Gateway target remains
`app.gateway.app:app`, the ordinary forced-command entry remains
`app.gateway.nexus_receiver.forced_command`, and `nexus_receiver.enabled`
remains false by default. The acceptance entrypoints refuse to start unless all
of these inputs are present and exact:

- `DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED=true`;
- profile `skill-hub-user-v1` and preparation revision `ad8600e3...`;
- the reviewed harness revision in both the environment and materialized PR #29
  manifest;
- one Gateway worker;
- the isolated `deerflow_acceptance` PostgreSQL database, role, and `deerflow`
  schema;
- the frozen synthetic policy revisions and mounted principal-map Secret;
- external owner-only package staging and fault-state paths.

The acceptance overlay creates only the Deer Flow `postgres:17.5-alpine`
database. Its password comes from the external file named by
`DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_POSTGRES_PASSWORD_FILE`. The overlay builds
both Deer Flow acceptance images with the `postgres` extra and derives the
process-only `DATABASE_URL` from that mounted Secret; the value is not stored in
the image, Compose model, or repository. The separately materialized
`config.yaml` references `$DATABASE_URL`, selects schema `deerflow`, and never
contains the password. The later Nexus harness owns its separate database and
role; cross-database access is not configured here.

The restricted sshd starts only after creating `/run/sshd`, generating a
root-managed authorized-keys file readable by the dedicated account, and
building an explicit `SetEnv` file. Only the receiver configuration paths,
acceptance revision/profile paths, one-worker setting, release gate, and derived
`DATABASE_URL` are passed to forced-command sessions; arbitrary container or SSH
client environment variables are not forwarded.

The composition uses `SqlReceiverOperationStore`, `LocalReceiverPackageStore`,
the controlled three-user fixture directory, and `UserScopedReceiverInstaller`.
The native deterministic SkillScan runs before the synthetic acceptance trust
decision commits a package. Installation remains USER-only; activation uses the
normal user-scoped enabled-state and projection boundary.

## Fault control

The local container CLI arms one fault atomically and each owner consumes it once:

```bash
cd backend
PYTHONPATH=. uv run python -m app.gateway.nexus_receiver.acceptance_control \
  arm fail_activation_after_atomic_install
```

Supported fixture names are `disconnect_after_receiver_accept`,
`restart_nexus_after_outcome_unknown`,
`restart_deer_flow_before_activation`,
`fail_activation_after_atomic_install`, `disable_after_success`, and
`observation_unavailable`. The Nexus restart is driven by the later Nexus
harness; it claims that fault through the same atomic control before restarting
its own process:

```bash
cd backend
PYTHONPATH=. uv run python -m app.gateway.nexus_receiver.acceptance_control \
  consume restart_nexus_after_outcome_unknown
```

Deer Flow provides this deterministic bridge but does not implement or trigger
the external Nexus process restart.

`restart_deer_flow_before_activation` terminates the acceptance process only
after the native Skill tree is atomically installed disabled and the SQL
operation reaches `activating`. Restart recovery then consumes the original
operation. `disconnect_after_receiver_accept` closes the acceptance SSH command
only after package staging and SQL reservation are durable.

## Readiness only

Materialize the PR #29 example manifest outside both repositories, set
`revisions.deerFlowAcceptanceHarnessRevision` to the reviewed harness head, and
run:

```bash
./scripts/check-nexus-receiver-acceptance-readiness.sh \
  --manifest /absolute/path/to/materialized-manifest.json \
  --nexus-preparation-repo /absolute/path/to/clean-pr-29-worktree
```

`DEER_FLOW_ACCEPTANCE_HARNESS_READY` proves only revision, fixture, contract,
composition, and default-deny readiness. It does not start services or count as
joint E2E evidence.

The isolated PostgreSQL/native-installer integration test is opt-in and expects
an ephemeral `deerflow_acceptance` database owned by the matching role:

```bash
cd backend
DEERFLOW_NEXUS_RECEIVER_ACCEPTANCE_POSTGRES_URL=postgresql://deerflow_acceptance:<password>@127.0.0.1:<port>/deerflow_acceptance \
  uv run --extra postgres pytest \
  tests/test_nexus_receiver_acceptance.py::test_acceptance_composition_uses_isolated_postgres_and_native_user_installer
```

The later isolated run layers these files explicitly:

```bash
docker compose \
  -f docker/docker-compose.yaml \
  -f docker/docker-compose.nexus-receiver-ssh.yaml \
  -f docker/docker-compose.nexus-receiver-acceptance.yaml \
  --profile nexus-receiver-ssh config --quiet
```

Production Secrets, enterprise principal/RBAC, directory privacy, trust and
compatibility policy, multi-process recovery ownership, and native GLOBAL
semantics remain separate blocked gates.
