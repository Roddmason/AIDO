"""Anti-drift checks between the local-runtime documentation and the code contracts it describes.

Every expectation is derived from the productive module (catalog, causes, settings registry,
workload profiles) so a document cannot keep describing a contract that no longer exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from local_control_center.agents.local_runtime_causes import LOCAL_RUNTIME_CAUSES
from local_control_center.agents.provider_catalog import PROVIDER_CATALOG, ProviderCatalogEntry
from local_control_center.host_resources.models import WorkloadProfile
from local_control_center.host_resources.profiles import GIB, LOCAL_INFERENCE_CLASSES, WORKLOAD_PROFILES
from local_control_center.settings.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PROVIDERS_DOC = "docs/runtime-providers.md"
RESOURCE_PROFILES_DOC = "docs/operational-hardening/p0-resource-profiles.md"
EXPECTED_LOCAL_CATALOG_IDS = {"llama_cpp", "lm_studio", "vllm", "local_openai_compatible"}
OPENAPI_CLIENT = ROOT / "local-control-center" / "web" / "src" / "api" / "generated" / "openapi.ts"
LOCAL_ROUTE_PREFIXES = ("/api/v1/local-endpoints", "/api/v1/local-runtimes")
OPENAPI_OPERATION = re.compile(
    r'"method": "(?P<method>[A-Z]+)", "operationId": "[^"]+", "path": "(?P<path>[^"]+)"'
)
DOC_ROUTE = re.compile(r"`(?P<method>GET|POST|PUT|PATCH|DELETE) (?P<path>/api/v1/local-[^`\s]+)`")
PATH_PARAMETER = re.compile(r"\{[^}]+\}")
QUEUED_LOCAL_ROUTES = (
    "POST /api/v1/local-endpoints/{provider_id}/validate-model",
    "POST /api/v1/local-runtimes/discover",
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _table_row(doc: str, first_cell: str) -> str:
    rows = [line for line in doc.splitlines() if line.startswith(f"| {first_cell} |")]
    assert len(rows) == 1, f"expected one table row for {first_cell}, found {len(rows)}"
    return rows[0]


def _local_catalog_entries() -> list[ProviderCatalogEntry]:
    return [entry for entry in PROVIDER_CATALOG if entry.local_profile is not None]


def _admission_label(profile: WorkloadProfile) -> str:
    kind = "essential" if profile.essential else "heavy" if profile.heavy else "light"
    return f"{kind}, GPU" if profile.gpu_required else kind


def test_runtime_providers_doc_has_one_row_per_local_catalog_entry() -> None:
    doc = _read(RUNTIME_PROVIDERS_DOC)
    entries = _local_catalog_entries()
    assert {entry.id for entry in entries} >= EXPECTED_LOCAL_CATALOG_IDS
    for entry in entries:
        profile = entry.local_profile
        assert profile is not None
        row = _table_row(doc, f"`{entry.id}`")
        base_url_cell = f"`{entry.default_base_url}`" if entry.default_base_url else "none (required)"
        assert base_url_cell in row, (entry.id, row)
        assert f"`{profile.liveness_path}`" in row, (entry.id, row)
        assert f"`{profile.model_state_source}`" in row, (entry.id, row)
        assert f"| {profile.cold_start_timeout_s} s |" in row, (entry.id, row)


def test_runtime_providers_doc_explains_every_local_runtime_cause() -> None:
    doc = _read(RUNTIME_PROVIDERS_DOC)
    for cause in sorted(LOCAL_RUNTIME_CAUSES):
        _table_row(doc, f"`{cause}`")


def test_runtime_providers_doc_documents_local_settings_and_mode() -> None:
    doc = _read(RUNTIME_PROVIDERS_DOC)
    descriptors = {descriptor.key: descriptor for descriptor in REGISTRY}
    local_keys = sorted(key for key in descriptors if key.startswith("runtime.local."))
    assert {"runtime.local.enabled", "runtime.local.maxCallSeconds"} <= set(local_keys)
    for key in local_keys:
        assert f"`{key}`" in doc, key
    max_call = descriptors["runtime.local.maxCallSeconds"]
    expected_range = f"(default {max_call.default:g} s, range {max_call.minimum:g}-{max_call.maximum:g} s)"
    assert expected_range in doc
    assert "local" in (descriptors["project.runtime.defaultMode"].enum or ())
    assert "`project.runtime.defaultMode` accepts `local`" in doc
    assert "Local runtimes do not need `AIDO_ENABLE_REAL_PROVIDER_CALLS`" in doc


def test_resource_docs_match_local_inference_profiles() -> None:
    profiles_doc = _read(RESOURCE_PROFILES_DOC)
    for workload_class in sorted(LOCAL_INFERENCE_CLASSES):
        profile = WORKLOAD_PROFILES[workload_class]
        expected_row = (
            f"| {workload_class} | {_admission_label(profile)} | {profile.cpu_limit_percent:g} | "
            f"{profile.memory_limit_bytes // GIB} | {profile.process_limit} |"
        )
        assert _table_row(profiles_doc, workload_class) == expected_row
    assert "`LOCAL_INFERENCE_CLASSES`" in profiles_doc
    assert "`local_model_call`" in _read(RUNTIME_PROVIDERS_DOC)


def _normalized_route(method: str, path: str) -> tuple[str, str]:
    return method, PATH_PARAMETER.sub("{id}", path)


def _openapi_local_routes() -> set[tuple[str, str]]:
    text = OPENAPI_CLIENT.read_text(encoding="utf-8")
    return {
        _normalized_route(match["method"], match["path"])
        for match in OPENAPI_OPERATION.finditer(text)
        if match["path"].startswith(LOCAL_ROUTE_PREFIXES)
    }


def test_model_gateway_doc_lists_exactly_the_local_endpoint_routes() -> None:
    expected = _openapi_local_routes()
    assert ("POST", "/api/v1/local-runtimes/discover") in expected
    assert ("DELETE", "/api/v1/local-endpoints/{id}") in expected
    doc = _read("docs/model-gateway.md")
    documented = {_normalized_route(match["method"], match["path"]) for match in DOC_ROUTE.finditer(doc)}
    assert documented == expected
    for queued_route in QUEUED_LOCAL_ROUTES:
        assert f"`{queued_route}`: queued (`202`" in doc, queued_route


def test_backend_doc_lists_the_local_runtimes_package() -> None:
    assert (ROOT / "local_control_center" / "local_runtimes" / "api.py").is_file()
    doc = _read("docs/backend.md")
    assert "- `local_runtimes`:" in doc
    assert "`/api/v1/local-endpoints`" in doc


def test_model_routing_doc_states_the_local_private_locality_rule() -> None:
    doc = _read("docs/model-routing.md")
    rule = (
        "`local_private` rejects `provider_type=api` and `provider_type=gateway`, and "
        "`provider_type=local` accounts whose endpoint locality is `remote`"
    )
    assert rule in doc
    assert "`declared_local`" in doc
    assert "docs/runtime-providers.md#local-runtimes" in doc
