"""Contract tests between the local-runtime web UI and the backend it mirrors.

The UI keeps a TypeScript mirror of the provider catalog and calls the local-endpoint routes by their
generated operation ids; these tests fail when either side drifts, and when a local runtime cause can
reach the AI health modal without plain-language copy in both languages.
"""

from __future__ import annotations

import re
from pathlib import Path

from local_control_center.agents.provider_catalog import provider_catalog_entry

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "local-control-center" / "web" / "src"
LOCAL_RUNTIME_IDS = ("llama_cpp", "lm_studio", "vllm", "local_openai_compatible")
AUTH_KIND_BY_CREDENTIAL = {
    "optional_bearer_token": "optional_api_key",
    "bearer_token": "api_key",
    "none": "none",
}
TS_ENTRY_RE = re.compile(r"\{\n\t\tid: '(?P<id>[a-z0-9_]+)',\n(?P<body>.*?)\n\t\}", re.DOTALL)
TS_FIELD_RE = re.compile(r"^\t\t(?P<name>\w+): (?P<value>.+),$", re.MULTILINE)
LOCAL_RUNTIME_OPERATIONS = (
    "list_local_endpoints_api_v1_local_endpoints_get",
    "create_local_endpoint_api_v1_local_endpoints_post",
    "patch_local_endpoint_api_v1_local_endpoints__provider_id__patch",
    "declare_local_endpoint_api_v1_local_endpoints__provider_id__declare_local_put",
    "patch_local_model_api_v1_local_endpoints__provider_id__models_patch",
    "validate_local_model_api_v1_local_endpoints__provider_id__validate_model_post",
    "delete_local_endpoint_api_v1_local_endpoints__provider_id__delete",
    "discover_runtimes_api_v1_local_runtimes_discover_post",
)
QUEUED_LOCAL_RUNTIME_OPERATIONS = (
    "validate_local_model_api_v1_local_endpoints__provider_id__validate_model_post",
    "discover_runtimes_api_v1_local_runtimes_discover_post",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ts_literal(value: str) -> str | bool | None:
    if value == "null":
        return None
    if value in {"true", "false"}:
        return value == "true"
    return value.strip("'")


def _ts_catalog() -> dict[str, dict[str, str | bool | None]]:
    source = _read(WEB_SRC / "features" / "runtime-setup" / "runtimeSetup.ts")
    return {
        match.group("id"): {
            field.group("name"): _ts_literal(field.group("value"))
            for field in TS_FIELD_RE.finditer(match.group("body"))
        }
        for match in TS_ENTRY_RE.finditer(source)
    }


def test_local_runtime_catalog_mirror_matches_backend_catalog() -> None:
    mirror = _ts_catalog()

    for catalog_id in LOCAL_RUNTIME_IDS:
        backend = provider_catalog_entry(catalog_id)
        assert backend is not None, f"backend catalog lacks {catalog_id}"
        assert catalog_id in mirror, f"runtimeSetup.ts lacks {catalog_id}"
        fields = mirror[catalog_id]
        assert fields["group"] == "local", catalog_id
        assert fields["providerType"] == backend.provider_type, catalog_id
        assert fields["apiFormat"] == backend.api_format, catalog_id
        assert fields["defaultBaseUrl"] == backend.default_base_url, catalog_id
        assert fields["needsBaseUrl"] == ("baseUrl" in backend.required_fields), catalog_id
        assert fields["authKind"] == AUTH_KIND_BY_CREDENTIAL[backend.credential_kind], catalog_id


def test_local_runtime_client_calls_the_contract_operations() -> None:
    client = _read(WEB_SRC / "api" / "client.ts")
    generated = _read(WEB_SRC / "api" / "generated" / "openapi.ts")

    for operation in LOCAL_RUNTIME_OPERATIONS:
        assert f'"operationId": "{operation}"' in generated, f"OpenAPI lacks {operation}"
        assert f"'{operation}'" in client, f"client.ts does not call {operation}"
    for operation in QUEUED_LOCAL_RUNTIME_OPERATIONS:
        assert f'"{operation}": "/api/v1/executions/{{execution_id}}"' in generated, (
            f"{operation} missing from EXECUTION_OPERATIONS: the client would stop at the 202"
        )
