# Nexus Skill Receiver 1.1 Stable Error Table

Every Problem includes a correlation ID and redacted detail. Error codes are additive
within major version 1. Implementations must map internal exceptions to this registry and
must not expose paths, credentials, raw directory fields, scanner output, or upstream
bodies.

Authorization uses authorization before target resolution. For `skills.list`, an
unauthorized existing or missing USER target returns the same `FORBIDDEN` Problem.
Authorized callers may then receive target-specific errors. `operations.get` requires
`receiver:operations:read` plus the operation scope's observe action; missing authority
and a missing operation are both represented by the route's non-enumerating not-found
response policy.

| Code | HTTP | Retryable | Contract meaning |
| --- | ---: | :---: | --- |
| `AUTHENTICATION_REQUIRED` | 401 | no | Dedicated receiver machine identity is absent or invalid. |
| `FORBIDDEN` | 403 | no | Principal lacks the action or target entitlement; target existence is undisclosed. |
| `RATE_LIMITED` | 429 | yes | Principal/action/target or normalized-query budget is exhausted. |
| `RECEIVER_NOT_READY` | 503 | yes | Required receiver composition is not ready. |
| `CAPABILITY_UNSUPPORTED` | 409 | no | Requested capability is not implemented by this deployment. |
| `CAPABILITY_READ_ONLY` | 409 | no | Receiver is reachable but mutation is closed. |
| `USER_DIRECTORY_UNSUPPORTED` | 409 | no | Controlled user directory is not configured. |
| `USER_DIRECTORY_UNAVAILABLE` | 503 | yes | Controlled directory cannot answer safely. |
| `GLOBAL_INSTALL_UNSUPPORTED` | 409 | no | Native GLOBAL install capability remains closed. |
| `USER_INSTALL_UNSUPPORTED` | 409 | no | USER install capability is unavailable. |
| `GLOBAL_ACTIVATION_UNSUPPORTED` | 409 | no | Native GLOBAL activation/load proof is unavailable. |
| `TARGET_USER_NOT_FOUND` | 404 | no | Authorized exact USER target does not exist. |
| `TARGET_USER_NOT_ELIGIBLE` | 409 | no | Authorized USER target cannot receive installs. |
| `INVALID_INSTALLATION_TARGET` | 422 | no | Closed GLOBAL/USER target shape is invalid. |
| `SKILL_INCOMPATIBLE` | 422 | no | Package is incompatible with the receiver/runtime contract. |
| `COMPATIBILITY_UNKNOWN` | 503 | yes | Compatibility provider cannot make a fail-closed decision. |
| `TRUST_POLICY_NOT_CONFIGURED` | 503 | no | Approved signature/provenance policy is absent. |
| `SIGNATURE_REQUIRED` | 422 | no | Required signature or provenance evidence is absent or untrusted. |
| `PACKAGE_TOO_LARGE` | 413 | no | Raw, expanded, entry-count, or policy size limit is exceeded. |
| `PACKAGE_MEDIA_TYPE_UNSUPPORTED` | 415 | no | Package media type is not accepted. |
| `PACKAGE_INVALID` | 422 | no | Archive/framing is malformed, unsafe, or contains forbidden paths or links. |
| `PACKAGE_MANIFEST_MISMATCH` | 422 | no | Manifest name/version does not match the command identity. |
| `DIGEST_MISMATCH` | 422 | no | Raw package bytes do not match the declared digest. |
| `SKILL_ALREADY_INSTALLED` | 409 | no | Exact first-install target already contains the same identity. |
| `SKILL_NAME_CONFLICT` | 409 | no | Same runtime name resolves to another identity or protected namespace. |
| `OBSERVED_STATE_CONFLICT` | 409 | no | Current native Observed state differs from the submitted precondition. |
| `IDEMPOTENCY_KEY_REUSED` | 409 | no | Key is bound to another operation or canonical request. |
| `CURSOR_INVALID` | 422 | no | Cursor is malformed, altered, or replayed across principal, target, or query. |
| `CURSOR_EXPIRED` | 409 | no | Cursor expired or its catalog revision can no longer be reconstructed. |
| `OBSERVATION_UNAVAILABLE` | 503 | yes | Provider/metadata/load probe cannot distinguish installed, absent, or drift safely. |
| `ACTIVATION_FAILED` | 409 | depends | Install committed but activation/load proof failed; poll the durable operation. |
| `UPSTREAM_TIMEOUT` | 504 | yes | Outcome is unknown; recover by operation ID and never infer a terminal state. |
| `INTERNAL_ERROR` | 500 | no | Redacted unclassified receiver failure. |
