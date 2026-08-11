"""Provider conformance tests for DeerFlow's canonical Nexus receiver contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi" / "nexus-skill-receiver-v1.yaml"
FIXTURE_PATH = REPO_ROOT / "contracts" / "openapi" / "nexus-skill-receiver-v1.conformance.json"

EXPECTED_PATHS = {
    "/api/v1/nexus/skill-receiver/capabilities",
    "/api/v1/nexus/skill-receiver/users",
    "/api/v1/nexus/skill-receiver/operations",
    "/api/v1/nexus/skill-receiver/operations/{operationId}",
    "/api/v1/nexus/skill-receiver/observations/query",
}
EXPECTED_PHASES = {"accepted", "validating", "installing", "activating", "succeeded", "rejected", "failed"}
EXPECTED_TRANSITIONS = {
    "accepted": {"validating", "rejected"},
    "validating": {"installing", "rejected"},
    "installing": {"activating", "failed"},
    "activating": {"succeeded", "failed"},
    "succeeded": set(),
    "rejected": set(),
    "failed": set(),
}
EXPECTED_CAPABILITY_ENUMS = {
    "ReceiverTransportProfile": {"http_v1", "ssh_v1"},
    "ReceiverConnectionStatus": {"healthy", "unreachable", "unauthorized", "incompatible", "not_ready"},
    "ReceiverAccessMode": {"read_write", "read_only", "unsupported"},
    "ReceiverFreshness": {"current", "stale", "unavailable"},
    "CapabilitySupport": {"supported", "unsupported"},
    "ReceiverObservationMode": {"global_and_user", "global_only", "user_only", "unsupported"},
    "ReceiverActivationMode": {"global_and_user", "global_only", "user_only", "unsupported"},
}
EXPECTED_AUTHENTICATION_PROFILES = {
    "unapproved",
    "oauth2_client_credentials",
    "mtls",
    "combined",
    "ssh_forced_command",
}
EXPECTED_SSH_ACTIONS = {
    "capabilities.get": {
        "httpOperationId": "getNexusSkillReceiverCapabilities",
        "serviceActions": {"receiver:capabilities:read"},
        "requiredFrameFields": {"contractVersion", "correlationId"},
        "optionalFrameFields": set(),
        "successSchema": "ReceiverCapabilitySnapshot",
    },
    "users.list": {
        "httpOperationId": "listNexusSkillReceiverUsers",
        "serviceActions": {"receiver:user-directory:read"},
        "requiredFrameFields": {"contractVersion", "correlationId", "limit"},
        "optionalFrameFields": {"query", "cursor"},
        "successSchema": "ReceiverUserPage",
    },
    "install.submit": {
        "httpOperationId": "createNexusSkillReceiverOperation",
        "serviceActions": {"receiver:install:user"},
        "requiredFrameFields": {
            "contractVersion",
            "correlationId",
            "idempotencyKey",
            "requestSha256",
            "command",
        },
        "optionalFrameFields": set(),
        "successSchema": "ReceiverOperation",
    },
    "operations.get": {
        "httpOperationId": "getNexusSkillReceiverOperation",
        "serviceActions": {"receiver:operations:read"},
        "requiredFrameFields": {"contractVersion", "correlationId", "operationId"},
        "optionalFrameFields": set(),
        "successSchema": "ReceiverOperation",
    },
    "observations.query": {
        "httpOperationId": "queryNexusSkillReceiverObservation",
        "serviceActions": {"receiver:observe:user"},
        "requiredFrameFields": {"contractVersion", "correlationId", "query"},
        "optionalFrameFields": set(),
        "successSchema": "ObservedSkillInstallation",
    },
}
EXPECTED_PROVIDER_ERROR_CODES = {
    "AUTHENTICATION_REQUIRED",
    "FORBIDDEN",
    "RATE_LIMITED",
    "RECEIVER_NOT_READY",
    "CAPABILITY_UNSUPPORTED",
    "CAPABILITY_READ_ONLY",
    "USER_DIRECTORY_UNSUPPORTED",
    "USER_DIRECTORY_UNAVAILABLE",
    "GLOBAL_INSTALL_UNSUPPORTED",
    "USER_INSTALL_UNSUPPORTED",
    "GLOBAL_ACTIVATION_UNSUPPORTED",
    "TARGET_USER_NOT_FOUND",
    "TARGET_USER_NOT_ELIGIBLE",
    "INVALID_INSTALLATION_TARGET",
    "SKILL_INCOMPATIBLE",
    "COMPATIBILITY_UNKNOWN",
    "TRUST_POLICY_NOT_CONFIGURED",
    "SIGNATURE_REQUIRED",
    "PACKAGE_TOO_LARGE",
    "PACKAGE_MEDIA_TYPE_UNSUPPORTED",
    "PACKAGE_INVALID",
    "PACKAGE_MANIFEST_MISMATCH",
    "DIGEST_MISMATCH",
    "SKILL_ALREADY_INSTALLED",
    "SKILL_NAME_CONFLICT",
    "OBSERVED_STATE_CONFLICT",
    "IDEMPOTENCY_KEY_REUSED",
    "ACTIVATION_FAILED",
    "UPSTREAM_TIMEOUT",
    "INTERNAL_ERROR",
}
FORBIDDEN_AUTHORITY_HEADERS = {
    "X-User-ID",
    "X-Actor",
    "X-Environment",
    "X-Middleware-Instance",
    "X-Internal-Token",
}


def _load_contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def _load_fixtures() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _resolve_pointer(document: dict, pointer: str) -> object:
    value: object = document
    for raw_part in pointer.removeprefix("#/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        assert isinstance(value, dict)
        value = value[part]
    return value


def _dereference(document: dict, value: object, seen: frozenset[str] = frozenset()) -> object:
    if isinstance(value, list):
        return [_dereference(document, item, seen) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        assert isinstance(reference, str) and reference.startswith("#/"), f"Only local refs are allowed: {reference}"
        assert reference not in seen, f"Recursive schema is not supported by this conformance harness: {reference}"
        target = copy.deepcopy(_resolve_pointer(document, reference))
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if siblings:
            target = {"allOf": [target, siblings]}
        return _dereference(document, target, seen | {reference})
    return {key: _dereference(document, item, seen) for key, item in value.items()}


def _schema(contract: dict, name: str) -> dict:
    schema = _dereference(contract, contract["components"]["schemas"][name])
    assert isinstance(schema, dict)
    Draft202012Validator.check_schema(schema)
    return schema


def _validation_errors(contract: dict, schema_name: str, value: object) -> list[str]:
    validator = Draft202012Validator(_schema(contract, schema_name), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda error: [str(part) for part in error.path])
    return [error.message for error in errors]


def _operations(contract: dict):
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                yield path, method, operation


def _response(contract: dict, response: dict) -> dict:
    resolved = _dereference(contract, response)
    assert isinstance(resolved, dict)
    return resolved


def _parameter_name(contract: dict, parameter: dict) -> str:
    resolved = _dereference(contract, parameter)
    assert isinstance(resolved, dict)
    return resolved["name"]


def _operation_semantic_errors(operation: dict) -> list[str]:
    errors: list[str] = []
    terminal = operation["phase"] in {"succeeded", "rejected", "failed"}
    if terminal != (operation["completedAt"] is not None):
        errors.append("terminal phase and completedAt disagree")
    if operation["phase"] == "succeeded":
        observed = operation["observed"]
        if observed is None:
            return errors + ["succeeded operation has no Observed state"]
        exact_pairs = (
            ("target", operation["target"], observed["target"]),
            ("skillVersionId", operation["skillVersionId"], observed["skillVersionId"]),
            ("runtimeSkillName", operation["runtimeSkillName"], observed["runtimeSkillName"]),
            ("packageDigest", operation["packageDigest"], observed["packageDigest"]),
        )
        errors.extend(f"Observed {name} is not exact" for name, expected, actual in exact_pairs if expected != actual)
        if observed["presence"] != "installed":
            errors.append("Observed presence is not installed")
        if observed["enabled"] is not True:
            errors.append("Observed enabled is not true")
        if observed["loadState"] != "loaded":
            errors.append("Observed loadState is not loaded")
        if operation["error"] is not None:
            errors.append("succeeded operation carries an error")
    if operation["phase"] in {"rejected", "failed"} and operation["error"] is None:
        errors.append("unsuccessful terminal operation has no stable error")
    return errors


def test_contract_is_the_versioned_receiver_source_of_truth() -> None:
    contract = _load_contract()
    fixtures = _load_fixtures()

    assert contract["openapi"] == "3.1.0"
    assert contract["info"]["version"] == fixtures["contractVersion"] == "1.0.0"
    assert contract["x-receiver-contract"]["canonical"] is True
    assert contract["x-receiver-contract"]["compatibleMajor"] == 1
    assert contract["x-receiver-contract"]["legacyFallback"] == "forbidden"
    assert contract["x-receiver-contract"]["legacyPath"] == "/api/skills"
    assert set(contract["paths"]) == EXPECTED_PATHS
    assert all(path.startswith("/api/v1/nexus/skill-receiver/") for path in contract["paths"])


def test_operation_ids_headers_and_service_authorization_are_explicit() -> None:
    contract = _load_contract()
    operation_ids: list[str] = []

    for path, method, operation in _operations(contract):
        operation_ids.append(operation["operationId"])
        parameter_names = {_parameter_name(contract, item) for item in operation.get("parameters", [])}
        assert parameter_names.isdisjoint(FORBIDDEN_AUTHORITY_HEADERS)
        assert "X-Correlation-ID" in parameter_names, f"{method.upper()} {path} has no correlation request header"
        assert operation.get("security"), f"{method.upper()} {path} has no service security declaration"
        assert operation.get("x-required-service-actions") or operation.get("x-required-service-actions-by-target")
        for status, response in operation["responses"].items():
            resolved = _response(contract, response)
            assert "X-Correlation-ID" in resolved.get("headers", {}), f"{method.upper()} {path} {status} has no correlation response header"

    assert len(operation_ids) == len(set(operation_ids))
    assert set(contract["x-receiver-contract"]["forbiddenAuthorityHeaders"]) == FORBIDDEN_AUTHORITY_HEADERS

    parameters = contract["components"]["parameters"]
    assert parameters["CorrelationId"]["schema"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": "^[A-Za-z0-9._:/-]+$",
    }
    assert parameters["IdempotencyKey"]["schema"] == {
        "type": "string",
        "minLength": 16,
        "maxLength": 160,
        "pattern": "^[A-Za-z0-9._:-]+$",
    }
    assert parameters["RequestSha256"]["schema"] == {
        "type": "string",
        "pattern": "^sha256:[a-f0-9]{64}$",
    }

    submit = contract["paths"]["/api/v1/nexus/skill-receiver/operations"]["post"]
    submit_parameters = {_parameter_name(contract, item) for item in submit["parameters"]}
    assert {"X-Correlation-ID", "Idempotency-Key", "X-Request-SHA256"} <= submit_parameters
    accepted = _response(contract, submit["responses"]["202"])
    assert {"X-Correlation-ID", "Location"} <= set(accepted["headers"])
    assert "multipart/form-data" in submit["requestBody"]["content"]


def test_directory_is_bounded_cursor_search_without_exact_user_lookup() -> None:
    contract = _load_contract()
    operation = contract["paths"]["/api/v1/nexus/skill-receiver/users"]["get"]
    parameters = {_parameter_name(contract, item): _dereference(contract, item) for item in operation["parameters"]}

    assert set(parameters) == {"X-Correlation-ID", "query", "cursor", "limit"}
    assert parameters["query"]["schema"]["maxLength"] == 100
    assert parameters["cursor"]["schema"]["maxLength"] == 500
    assert parameters["limit"]["schema"] == {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}


def test_closed_enums_and_state_machine_are_exhaustive() -> None:
    contract = _load_contract()
    schemas = contract["components"]["schemas"]

    assert set(schemas["InstallationScope"]["enum"]) == {"GLOBAL", "USER"}
    assert set(schemas["ReceiverOperationPhase"]["enum"]) == EXPECTED_PHASES
    assert {key: set(value) for key, value in schemas["ReceiverOperationPhase"]["x-transitions"].items()} == EXPECTED_TRANSITIONS
    assert set(schemas["ReceiverOperationPhase"]["x-terminal-values"]) == {"succeeded", "rejected", "failed"}
    for name, expected in EXPECTED_CAPABILITY_ENUMS.items():
        assert set(schemas[name]["enum"]) == expected
    assert set(schemas["ReceiverAuthenticationProfile"]["enum"]) == EXPECTED_AUTHENTICATION_PROFILES

    version_policy = contract["x-receiver-contract"]["versionPolicy"]
    assert version_policy["closedEnumsRequireMajor"] is True
    assert version_policy["optionalResponseFieldsAreAdditive"] is True
    assert version_policy["additiveErrorCodesRequireMinor"] is True
    assert version_policy["authorityOrHashChangesRequireMajor"] is True


def test_ssh_binding_reuses_canonical_components_and_has_closed_wire_rules() -> None:
    contract = _load_contract()
    receiver_contract = contract["x-receiver-contract"]
    bindings = receiver_contract["bindings"]

    assert set(bindings) == {"http_v1", "ssh_v1"}
    assert bindings["http_v1"]["kind"] == "http"

    ssh = bindings["ssh_v1"]
    assert ssh["kind"] == "ssh"
    assert ssh["authenticationProfile"] == "ssh_forced_command"
    assert ssh["targetScopes"] == ["USER"]
    assert ssh["forcedCommand"] == "nexus-skill-receiver-v1"
    assert ssh["argvGrammar"] == "^nexus-skill-receiver-v1 (capabilities\\.get|users\\.list|install\\.submit|operations\\.get|observations\\.query)$"
    assert set(ssh["forbiddenFeatures"]) == {
        "interactive_shell",
        "pty",
        "agent_forwarding",
        "port_forwarding",
        "x11_forwarding",
        "scp",
        "sftp",
    }

    request = ssh["request"]
    assert request == {
        "jsonSerialization": "RFC 8785 JSON Canonicalization Scheme encoded as UTF-8",
        "maximumJsonFrameBytes": 65536,
        "frameDelimiter": "LF",
        "packageAction": "install.submit",
        "packageEncoding": "raw_octets",
        "packageLengthField": "command.packageSizeBytes",
        "packageDigestField": "command.packageDigest",
        "endOfRequest": "EOF",
    }
    response = ssh["response"]
    assert response["jsonSerialization"] == "RFC 8785 JSON Canonicalization Scheme encoded as UTF-8"
    assert response["documentCount"] == 1
    assert response["terminator"] == "LF_then_EOF"
    assert response["stdoutOtherBytes"] == "forbidden"
    assert response["successExitCode"] == 0
    assert response["problemExitCode"] == 10
    assert response["problemSchema"] == {"$ref": "#/components/schemas/ReceiverProblem"}

    assert set(ssh["actions"]) == set(EXPECTED_SSH_ACTIONS)
    http_operations = {operation["operationId"]: operation for _, _, operation in _operations(contract)}
    for action, expected in EXPECTED_SSH_ACTIONS.items():
        mapping = ssh["actions"][action]
        assert mapping["httpOperationId"] == expected["httpOperationId"]
        assert set(mapping["serviceActions"]) == expected["serviceActions"]
        assert set(mapping["requiredFrameFields"]) == expected["requiredFrameFields"]
        assert set(mapping["optionalFrameFields"]) == expected["optionalFrameFields"]
        assert mapping["successSchema"] == {"$ref": f"#/components/schemas/{expected['successSchema']}"}
        for value in mapping.values():
            if isinstance(value, dict) and "$ref" in value:
                assert value["$ref"].startswith("#/components/schemas/")
        assert "schemas" not in mapping

        http_operation = http_operations[expected["httpOperationId"]]
        if "x-required-service-actions" in http_operation:
            assert set(http_operation["x-required-service-actions"]) == expected["serviceActions"]
        else:
            assert set(http_operation["x-required-service-actions-by-target"]["USER"]) == expected["serviceActions"]

    assert ssh["actions"]["install.submit"]["commandSchema"] == {"$ref": "#/components/schemas/ReceiverInstallCommand"}
    assert ssh["actions"]["install.submit"]["unsupportedTargetProblemCode"] == "GLOBAL_INSTALL_UNSUPPORTED"
    assert ssh["actions"]["observations.query"]["querySchema"] == {"$ref": "#/components/schemas/ObservationQuery"}


def test_ssh_action_conformance_frames_reuse_http_parameter_and_component_rules() -> None:
    contract = _load_contract()
    fixtures = _load_fixtures()
    ssh = contract["x-receiver-contract"]["bindings"]["ssh_v1"]
    cases = fixtures["sshActionCases"]

    assert {case["action"] for case in cases} == set(EXPECTED_SSH_ACTIONS)
    for case in cases:
        action = case["action"]
        mapping = ssh["actions"][action]
        frame = case["frame"]
        required = set(mapping["requiredFrameFields"])
        allowed = required | set(mapping["optionalFrameFields"])

        assert case["argv"] == f"{ssh['forcedCommand']} {action}"
        assert required <= set(frame) <= allowed
        assert frame["contractVersion"] == fixtures["contractVersion"]
        assert not _validation_errors(contract, "SemVer", frame["contractVersion"])
        correlation_schema = contract["components"]["parameters"]["CorrelationId"]["schema"]
        assert not list(Draft202012Validator(correlation_schema).iter_errors(frame["correlationId"]))

        if action == "users.list":
            for field, parameter_name in (("query", "DirectoryQuery"), ("cursor", "Cursor"), ("limit", "Limit")):
                if field in frame:
                    schema = contract["components"]["parameters"][parameter_name]["schema"]
                    assert not list(Draft202012Validator(schema).iter_errors(frame[field]))
        elif action == "install.submit":
            assert not _validation_errors(contract, "ReceiverInstallCommand", frame["command"])
            for field, parameter_name in (("idempotencyKey", "IdempotencyKey"), ("requestSha256", "RequestSha256")):
                schema = contract["components"]["parameters"][parameter_name]["schema"]
                assert not list(Draft202012Validator(schema).iter_errors(frame[field]))
            assert case["packageByteCount"] == frame["command"]["packageSizeBytes"]
        elif action == "operations.get":
            schema = contract["components"]["parameters"]["OperationId"]["schema"]
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            assert not list(validator.iter_errors(frame["operationId"]))
        elif action == "observations.query":
            assert not _validation_errors(contract, "ObservationQuery", frame["query"])

        if action != "install.submit":
            assert "packageByteCount" not in case


def test_ssh_binding_rejects_global_without_changing_the_shared_command_schema() -> None:
    contract = _load_contract()
    fixtures = _load_fixtures()
    case = fixtures["sshBindingProblemCases"][0]

    assert case["action"] == "install.submit"
    assert case["exitCode"] == 10
    assert not _validation_errors(contract, "ReceiverInstallCommand", case["command"])
    assert not _validation_errors(contract, "ReceiverProblem", case["problem"])
    assert case["command"]["target"] == {"scope": "GLOBAL"}
    assert case["problem"]["code"] == "GLOBAL_INSTALL_UNSUPPORTED"


def test_error_code_is_open_but_known_provider_codes_are_unique() -> None:
    contract = _load_contract()
    error_schema = contract["components"]["schemas"]["ReceiverErrorCode"]
    known = error_schema["x-known-values"]

    assert error_schema["pattern"] == "^[A-Z][A-Z0-9_]*$"
    assert "enum" not in error_schema
    assert len(known) == len(set(known))
    assert set(known) == EXPECTED_PROVIDER_ERROR_CODES


def test_schema_conformance_fixtures_cover_positive_and_negative_cases() -> None:
    contract = _load_contract()
    fixtures = _load_fixtures()
    validity = {True: 0, False: 0}

    for case in fixtures["schemaCases"]:
        errors = _validation_errors(contract, case["schema"], case["value"])
        validity[case["valid"]] += 1
        assert (not errors) is case["valid"], f"{case['name']}: {errors}"

    assert validity[True] >= 5
    assert validity[False] >= 5


def test_operation_semantic_fixtures_require_exact_observed_activation() -> None:
    contract = _load_contract()
    fixtures = _load_fixtures()

    for case in fixtures["operationSemanticCases"]:
        schema_errors = _validation_errors(contract, "ReceiverOperation", case["value"])
        assert not schema_errors, f"{case['name']} is not structurally valid: {schema_errors}"
        semantic_errors = _operation_semantic_errors(case["value"])
        assert (not semantic_errors) is case["valid"], f"{case['name']}: {semantic_errors}"


def test_phase_transition_fixtures_match_the_contract_graph() -> None:
    contract = _load_contract()
    fixtures = _load_fixtures()
    transitions = {phase: set(next_phases) for phase, next_phases in contract["components"]["schemas"]["ReceiverOperationPhase"]["x-transitions"].items()}

    for case in fixtures["phaseTransitions"]:
        assert (case["to"] in transitions[case["from"]]) is case["valid"], case


def test_published_examples_are_schema_valid_and_default_deny() -> None:
    contract = _load_contract()
    paths = contract["paths"]

    capabilities = paths["/api/v1/nexus/skill-receiver/capabilities"]["get"]["responses"]["200"]["content"]["application/json"]
    capability_example = capabilities["examples"]["defaultDeny"]["value"]
    assert not _validation_errors(contract, "ReceiverCapabilitySnapshot", capability_example)
    assert capability_example["accessMode"] != "read_write"
    assert capability_example["authorization"] == {"profile": "unapproved", "grantedActions": []}

    users = paths["/api/v1/nexus/skill-receiver/users"]["get"]["responses"]["200"]["content"]["application/json"]
    assert not _validation_errors(contract, "ReceiverUserPage", users["examples"]["eligibleUsers"]["value"])

    submit_content = paths["/api/v1/nexus/skill-receiver/operations"]["post"]["requestBody"]["content"]["multipart/form-data"]
    for example in submit_content["examples"].values():
        assert not _validation_errors(contract, "ReceiverInstallMultipartRequest", example["value"])

    observation_content = paths["/api/v1/nexus/skill-receiver/observations/query"]["post"]["responses"]["200"]["content"]["application/json"]
    for example in observation_content["examples"].values():
        assert not _validation_errors(contract, "ObservedSkillInstallation", example["value"])
