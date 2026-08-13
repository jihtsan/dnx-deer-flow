# ADR 001: Nexus Skill Receiver GLOBAL Visibility and Recovery Ownership

- Status: Accepted for contract design; runtime implementation pending
- Date: 2026-08-13
- Contract: `contracts/openapi/nexus-skill-receiver-v1.yaml` 1.1.0

## Context

Nexus owns Hub Skill versions, publication approval, and desired state. DeerFlow owns
its installed trees, enabled/load state, catalog revision, operations, leases, and
Observed timestamps. Nexus must not infer DeerFlow state by scanning user directories.

The current receiver implements exact USER first installation. Native GLOBAL storage,
cross-process catalog invalidation, production trust/directory providers, and one
deployment-wide recovery coordinator are absent. Therefore GLOBAL remains unsupported
and `capabilities.get` must not advertise global support from this ADR alone.

## Decision

### Storage and commit boundary

Receiver-managed GLOBAL Skills use the native managed integration namespace
`integrations/skills/nexus/{runtimeSkillName}`. They never use `skills/custom` and never
materialize copies below individual user directories. Validation and extraction occur
under an invisible staging root on the same filesystem. Activation is one atomic rename
into the managed integration namespace after scope, name, digest, Manifest, trust,
compatibility, and current Observed state are revalidated.

The receiver rejects GLOBAL names that conflict with built-in public or integration
names. A user's same-name custom Skill retains DeerFlow's normal precedence; that user's
effective load is drift and cannot be reported as proof that the GLOBAL operation loaded.

### Visibility

After atomic activation, GLOBAL is available to existing users and future users without
walking or rewriting `users/`. A running run keeps the catalog captured when that run was
built. It does not change prompt or tool surfaces mid-run. The next run in the same thread
and a new session build from the current catalog.

Activation commits a durable catalog revision in PostgreSQL. Every Gateway and worker
includes that catalog revision in agent and sandbox-projection cache keys. A revision
change causes reload when the next run or sandbox acquire is built. Process-local cache
invalidation is an optimization, never the consistency mechanism.

GLOBAL `loaded` means the shared tree identity is exact, the global catalog is active,
and at least one real runtime consumer load probe succeeded. It does not mean every
existing session was mutated. A failure on another consumer is exposed as unavailable
or drift; it does not roll back a catalog already published to other consumers.

### Recovery ownership

PostgreSQL is authoritative for operations and recovery ownership. All receiver
transports and Gateway instances may submit and query operations, but only the holder of
a PostgreSQL singleton lease coordinates recovery for the deployment. A local Unix
datagram wake-up may reduce latency but is not part of correctness.

Each operation separately has an execution lease with renewal, compare-and-swap state
transitions, and a fencing token. A former owner must be unable to renew or transition
after takeover. The coordinator stops claiming work immediately after losing its
singleton lease; its workers stop after losing their execution lease. A successor waits
for expiry, claims with a new fencing token, revalidates all mutation inputs, and resumes
from durable phase and staged package state.

The shared phase graph remains `accepted -> validating -> installing -> activating ->
succeeded`, with validation rejection and post-validation failure branches defined by
OpenAPI. Timeout or disconnect returns an operation ID or outcome-unknown Problem;
clients recover only through `operations.get` and no transport fabricates a terminal
phase.

### Capability gate

GLOBAL remains unsupported until native storage, durable catalog revision, load probe,
scope-specific RBAC, trust and compatibility providers, shared volume topology, and
singleton recovery ownership all pass startup validation. Missing any gate prevents
receiver assembly and must not be converted into a warning or USER fallback.

## Consequences

- GLOBAL installation is one shared native catalog change, not a per-user fan-out.
- Existing runs are stable; subsequent runs converge through the durable catalog revision.
- Multi-Gateway correctness depends on PostgreSQL leases and fencing, not local sockets.
- This contract phase can publish semantics and fixtures while keeping GLOBAL capability
  closed until later implementation and production validation.
