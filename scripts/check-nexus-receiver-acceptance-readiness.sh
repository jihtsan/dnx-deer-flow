#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEER_FLOW_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST=""
NEXUS_PREPARATION_REPO=""

EXPECTED_BASE_REVISION="e2fa130f24c12de4f74d4b3b2977ae92c3f7fbd4"
EXPECTED_RUNTIME_REVISION="1acaca4e553d83fecfdae04b038f7d415edae6e2"
EXPECTED_PREPARATION_REVISION="ad8600e3ec4282c33fba896be2bb62f1d6674f3b"
EXPECTED_OPENAPI_SHA256="bf3c6a98c8686695cd6518f27817b2f1b184acab17863dc2ae5c303ae9c24615"
EXPECTED_CONFORMANCE_SHA256="300e4be74bf66749718d4bdc0d7845ed51dc4e4f0498be8c1fbe47ef871fe81c"
EXPECTED_FAULTS='["disconnect_after_receiver_accept","restart_nexus_after_outcome_unknown","restart_deer_flow_before_activation","fail_activation_after_atomic_install","disable_after_success","observation_unavailable"]'

fail() {
  echo "DEER_FLOW_ACCEPTANCE_HARNESS_NOT_READY: $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: check-nexus-receiver-acceptance-readiness.sh \
  --manifest <materialized-manifest.json> \
  --nexus-preparation-repo <clean-pr-29-worktree>

This command is read-only. It does not start PostgreSQL, Gateway, sshd, Nexus,
or a joint E2E run.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest)
      [[ $# -ge 2 ]] || fail "--manifest requires a value"
      MANIFEST="$2"
      shift 2
      ;;
    --nexus-preparation-repo)
      [[ $# -ge 2 ]] || fail "--nexus-preparation-repo requires a value"
      NEXUS_PREPARATION_REPO="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

for command in git jq grep; do
  command -v "$command" >/dev/null 2>&1 || fail "required command is unavailable: $command"
done
if command -v sha256sum >/dev/null 2>&1; then
  SHA256_COMMAND=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
  SHA256_COMMAND=(shasum -a 256)
else
  fail "sha256sum or shasum is required"
fi

[[ -f "$MANIFEST" ]] || fail "materialized manifest is unavailable"
git -C "$NEXUS_PREPARATION_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Nexus preparation path is not a Git worktree"
[[ "$(git -C "$NEXUS_PREPARATION_REPO" rev-parse HEAD)" == "$EXPECTED_PREPARATION_REVISION" ]] || fail "Nexus preparation worktree is not at PR #29 head"
[[ -z "$(git -C "$NEXUS_PREPARATION_REPO" status --porcelain --untracked-files=all)" ]] || fail "Nexus preparation worktree is not clean"

HEAD_REVISION="$(git -C "$DEER_FLOW_REPO" rev-parse HEAD)"
git -C "$DEER_FLOW_REPO" merge-base --is-ancestor "$EXPECTED_BASE_REVISION" "$HEAD_REVISION" || fail "harness does not descend from PR #18 head"
[[ -z "$(git -C "$DEER_FLOW_REPO" status --porcelain --untracked-files=all)" ]] || fail "Deer Flow harness worktree is not clean"

jq -e --arg base "$EXPECTED_BASE_REVISION" --arg runtime "$EXPECTED_RUNTIME_REVISION" --arg head "$HEAD_REVISION" '
  .schemaVersion == 1 and
  .executionPolicy.purpose == "preparation_rehearsal_only" and
  .executionPolicy.productionWriteEnabled == false and
  .executionPolicy.jointE2EExecuted == false and
  .executionPolicy.globalEnabled == false and
  .executionPolicy.fixtureSuccessIsAcceptance == false and
  .revisions.deerFlowDeploymentRevision == $base and
  .revisions.deerFlowReceiverRuntimeRevision == $runtime and
  .revisions.deerFlowAcceptanceHarnessRevision == $head and
  .topology.postgres.image == "postgres:17.5-alpine" and
  .topology.postgres.deerFlow.database == "deerflow_acceptance" and
  .topology.postgres.deerFlow.schema == "deerflow" and
  .topology.postgres.deerFlow.role == "deerflow_acceptance" and
  .topology.postgres.crossDatabaseAccess == false and
  .topology.nexus.publicWriteRoutesEnabled == false and
  .topology.deerFlow.gatewayWorkers == 1 and
  .topology.deerFlow.sshdBindHost == "127.0.0.1" and
  .topology.deerFlow.sshdPort == 38222 and
  .fixture.ssh.clientIdentitySecretReference == "secret://joint-acceptance/ssh/client-identity" and
  .fixture.ssh.hostKeySecretReference == "secret://joint-acceptance/ssh/host-key" and
  .fixture.ssh.principalMapSecretReference == "secret://joint-acceptance/ssh/principal-map" and
  .fixture.ssh.materialProvisioned == false and
  .fixture.nexusPrincipal.productionApproved == false and
  .fixture.policies.productionApproved == false and
  [.fixture.packages[].id] == ["valid-v1", "name-conflict-v2"] and
  all(.fixture.packages[];
    .mediaType == "application/zip" and
    (.path | startswith("/")) and
    (.digest | test("^sha256:[0-9a-f]{64}$")) and
    (.sizeBytes | type) == "number" and
    .sizeBytes > 0)
' "$MANIFEST" >/dev/null || fail "materialized PR #29 manifest violates harness pins or default-deny"
[[ "$(jq -c '.fixture.faults' "$MANIFEST")" == "$EXPECTED_FAULTS" ]] || fail "acceptance fault inventory drifted"

file_sha256() {
  "${SHA256_COMMAND[@]}" "$1" | awk '{print $1}'
}

OPENAPI="$DEER_FLOW_REPO/contracts/openapi/nexus-skill-receiver-v1.yaml"
CONFORMANCE="$DEER_FLOW_REPO/contracts/openapi/nexus-skill-receiver-v1.conformance.json"
[[ "$(file_sha256 "$OPENAPI")" == "$EXPECTED_OPENAPI_SHA256" ]] || fail "canonical OpenAPI checksum drifted"
[[ "$(file_sha256 "$CONFORMANCE")" == "$EXPECTED_CONFORMANCE_SHA256" ]] || fail "canonical conformance checksum drifted"

for path in \
  backend/app/gateway/nexus_receiver/acceptance.py \
  backend/app/gateway/nexus_receiver/acceptance_app.py \
  backend/app/gateway/nexus_receiver/acceptance_forced_command.py \
  backend/app/gateway/nexus_receiver/acceptance_control.py \
  docker/docker-compose.nexus-receiver-acceptance.yaml; do
  [[ -f "$DEER_FLOW_REPO/$path" ]] || fail "required acceptance harness file is missing: $path"
done

git -C "$DEER_FLOW_REPO" diff --quiet "$EXPECTED_BASE_REVISION" -- backend/app/gateway/app.py || fail "production Gateway composition root changed"
git -C "$DEER_FLOW_REPO" diff --quiet "$EXPECTED_BASE_REVISION" -- backend/app/gateway/nexus_receiver/forced_command.py || fail "production forced-command entry changed"
grep -Fq 'enabled: bool = False' "$DEER_FLOW_REPO/backend/packages/harness/deerflow/config/nexus_receiver_config.py" || fail "receiver default is no longer disabled"
grep -Fq 'DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED: ${DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED:-false}' "$DEER_FLOW_REPO/docker/docker-compose.nexus-receiver-acceptance.yaml" || fail "acceptance overlay is not default-disabled"
grep -Fq 'app.gateway.nexus_receiver.acceptance_app:app' "$DEER_FLOW_REPO/docker/docker-compose.nexus-receiver-acceptance.yaml" || fail "acceptance Gateway entrypoint is missing"
grep -Fq -- '--workers 1' "$DEER_FLOW_REPO/docker/docker-compose.nexus-receiver-acceptance.yaml" || fail "acceptance Gateway is not fixed to one worker"
grep -Fq 'NEXUS_RECEIVER_FORCED_COMMAND_PROFILE: acceptance' "$DEER_FLOW_REPO/docker/docker-compose.nexus-receiver-acceptance.yaml" || fail "acceptance forced-command profile is missing"
grep -Fq 'image: postgres:17.5-alpine' "$DEER_FLOW_REPO/docker/docker-compose.nexus-receiver-acceptance.yaml" || fail "acceptance PostgreSQL image is not pinned"
grep -Fq 'POSTGRES_DB: deerflow_acceptance' "$DEER_FLOW_REPO/docker/docker-compose.nexus-receiver-acceptance.yaml" || fail "acceptance PostgreSQL database is not isolated"
grep -Fq 'choices=("arm", "clear", "consume", "status")' "$DEER_FLOW_REPO/backend/app/gateway/nexus_receiver/acceptance_control.py" || fail "one-shot external fault consumer is missing"

echo "DEER_FLOW_ACCEPTANCE_HARNESS_READY"
echo "deer_flow_acceptance_harness_revision=$HEAD_REVISION"
echo "deer_flow_deployment_revision=$EXPECTED_BASE_REVISION"
echo "receiver_runtime_revision=$EXPECTED_RUNTIME_REVISION"
echo "preparation_revision=$EXPECTED_PREPARATION_REVISION"
echo "openapi_sha256=$EXPECTED_OPENAPI_SHA256"
echo "conformance_sha256=$EXPECTED_CONFORMANCE_SHA256"
echo "sql_operation_store=wired_for_acceptance_profile"
echo "native_user_installer=wired_for_acceptance_profile"
echo "production_bootstrap_present=false"
echo "production_write_enabled=false"
echo "joint_e2e_executed=false"
