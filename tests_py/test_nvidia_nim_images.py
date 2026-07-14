from __future__ import annotations

import base64
import hashlib
import os
import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.app import create_app
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from tests_py.control_plane_fixture import ControlPlaneFixture


def _png(width: int = 1, height: int = 1) -> bytes:
    def chunk(kind: bytes, content: bytes) -> bytes:
        checksum = zlib.crc32(kind + content) & 0xFFFFFFFF
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00" + (b"\x00\x00\x00" * max(width, 1)))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


PNG_BYTES = _png()
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")


class MemoryImageArtifactStore:
    def __init__(self) -> None:
        self.persisted: list[dict[str, object]] = []
        self.reads: list[tuple[str, str]] = []

    def require_project(self, *, project_id: str) -> None:
        if not project_id:
            raise RuntimeError("project_not_found")

    def read_image_data_url(self, *, project_id: str, artifact_id: str) -> str:
        self.reads.append((project_id, artifact_id))
        return f"data:image/png;base64,{PNG_BASE64}"

    def persist_image(
        self,
        *,
        project_id: str,
        provider_id: str,
        model: str,
        content: bytes,
    ):
        from local_control_center.agents.providers.capabilities import ImageArtifactReference

        self.persisted.append(
            {
                "projectId": project_id,
                "providerId": provider_id,
                "model": model,
                "content": content,
            }
        )
        return ImageArtifactReference(
            artifactId="artifact-generated",
            mimeType="image/png",
            sizeBytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            downloadPath=f"/api/v1/projects/{project_id}/artifacts/artifact-generated",
        )


def _create_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database = tmp_path / "platform.sqlite"
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(database))
    runtime = ControlPlaneFixture(cwd=tmp_path, db_path=database)
    runtime.init()
    return TestClient(create_app(runtime=runtime, static_dir=None))


def _auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def _create_project(database: Path, root: Path, *, name: str) -> dict[str, object]:
    with open_sqlite_connection(database) as connection:
        return ProjectsRepository(connection).create_project(
            name=name,
            path=root / name,
            template_id="other",
        )


def _enable_nvidia_policy(database: Path) -> None:
    with open_sqlite_connection(database) as connection:
        repository = RuntimeConfigRepository(connection)
        repository.set_runtime_setting("runtime.remote.enabled", True)
        repository.set_runtime_setting("runtime.nvidia.enabled", True)


@pytest.mark.parametrize(
    ("profile", "deployment_mode", "base_url", "expected_url", "response", "expected_payload"),
    [
        (
            "nvidia_qwen_image_generation_infer",
            "self_hosted_development",
            "http://127.0.0.1:8100/v1",
            "http://127.0.0.1:8100/v1/infer",
            {"artifacts": [{"base64": PNG_BASE64, "finishReason": "SUCCESS"}]},
            {
                "prompt": "A small observatory",
                "negative_prompt": "text",
                "width": 1024,
                "height": 768,
                "seed": 7,
                "steps": 30,
                "cfg_scale": 4.5,
                "samples": 1,
            },
        ),
        (
            "nvidia_openai_image_generation",
            "self_hosted_development",
            "http://127.0.0.1:8101/v1",
            "http://127.0.0.1:8101/v1/images/generations",
            {"data": [{"b64_json": PNG_BASE64}]},
            {
                "model": "qwen/qwen-image-2512",
                "prompt": "A small observatory",
                "negative_prompt": "text",
                "size": "1024x768",
                "seed": 7,
                "steps": 30,
                "cfg_scale": 4.5,
                "n": 1,
                "response_format": "b64_json",
            },
        ),
    ],
)
def test_self_hosted_generation_profiles_use_documented_routes_and_artifact_boundary(
    profile: str,
    deployment_mode: str,
    base_url: str,
    expected_url: str,
    response: dict[str, object],
    expected_payload: dict[str, object],
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageGenerationProvider,
        ImageGenerationRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimVisualProvider

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=200, jsonBody=response)

    artifacts = MemoryImageArtifactStore()
    provider = NvidiaNimVisualProvider(
        provider_id="nvidia-images-local",
        base_url=base_url,
        credential_ref="",
        deployment_mode=deployment_mode,
        api_family="image_generation",
        adapter_profile=profile,
        transport=transport,
        image_artifact_store=artifacts,
    )

    result = provider.generate_image(
        ImageGenerationRequest(
            projectId="project-images",
            model="qwen/qwen-image-2512",
            prompt="A small observatory",
            negativePrompt="text",
            width=1024,
            height=768,
            seed=7,
            steps=30,
            cfgScale=4.5,
        )
    )

    assert isinstance(provider, ImageGenerationProvider)
    assert [(call.url, call.headers.get("Authorization"), call.json_body) for call in calls] == [
        (expected_url, None, expected_payload)
    ]
    assert result.model_dump(by_alias=True) == {
        "providerId": "nvidia-images-local",
        "model": "qwen/qwen-image-2512",
        "artifacts": [
            {
                "artifactId": "artifact-generated",
                "mimeType": "image/png",
                "sizeBytes": len(PNG_BYTES),
                "sha256": hashlib.sha256(PNG_BYTES).hexdigest(),
                "downloadPath": "/api/v1/projects/project-images/artifacts/artifact-generated",
            }
        ],
    }
    assert artifacts.persisted[0]["content"] == PNG_BYTES
    assert PNG_BASE64 not in str(result.model_dump(by_alias=True))


def test_hosted_prompt_generation_uses_exact_model_root_and_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageGenerationRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimVisualProvider

    monkeypatch.setenv("NVIDIA_HOSTED_IMAGE_KEY", "hosted-image-secret")
    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"artifacts": [{"base64": PNG_BASE64, "finish_reason": "SUCCESS"}]},
        )

    provider = NvidiaNimVisualProvider(
        provider_id="nvidia-hosted-image",
        base_url="https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev",
        credential_ref="env:NVIDIA_HOSTED_IMAGE_KEY",
        deployment_mode="hosted_trial",
        api_family="image_generation",
        adapter_profile="nvidia_hosted_prompt_image_generation",
        transport=transport,
        image_artifact_store=MemoryImageArtifactStore(),
    )

    provider.generate_image(
        ImageGenerationRequest(
            projectId="project-hosted",
            model="black-forest-labs/flux.1-dev",
            prompt="A safe geometric poster",
            seed=12,
        )
    )

    assert calls[0].url == "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev"
    assert calls[0].headers["Authorization"] == "Bearer hosted-image-secret"
    assert calls[0].json_body == {"prompt": "A safe geometric poster", "seed": 12}


@pytest.mark.parametrize(
    ("profile", "expected_suffix", "response"),
    [
        (
            "nvidia_qwen_image_editing_infer",
            "/infer",
            {"artifacts": [{"base64": PNG_BASE64, "finishReason": "SUCCESS"}]},
        ),
        (
            "nvidia_openai_image_editing",
            "/images/edits",
            {"data": [{"b64_json": PNG_BASE64}]},
        ),
    ],
)
def test_self_hosted_editing_profiles_load_project_artifact_before_transport(
    profile: str,
    expected_suffix: str,
    response: dict[str, object],
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageEditingProvider,
        ImageEditingRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimVisualProvider

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=200, jsonBody=response)

    artifacts = MemoryImageArtifactStore()
    provider = NvidiaNimVisualProvider(
        provider_id="nvidia-image-edit",
        base_url="http://127.0.0.1:8102/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="image_editing",
        adapter_profile=profile,
        transport=transport,
        image_artifact_store=artifacts,
    )

    result = provider.edit_image(
        ImageEditingRequest(
            projectId="project-edit",
            model="qwen/qwen-image-edit-2511",
            prompt="Transform into watercolor",
            inputArtifactId="artifact-input",
            seed=4,
        )
    )

    assert isinstance(provider, ImageEditingProvider)
    assert artifacts.reads == [("project-edit", "artifact-input")]
    assert calls[0].url == f"http://127.0.0.1:8102/v1{expected_suffix}"
    assert calls[0].json_body["image"] == f"data:image/png;base64,{PNG_BASE64}"
    if profile == "nvidia_openai_image_editing":
        assert calls[0].json_body["model"] == "qwen/qwen-image-edit-2511"
        assert calls[0].json_body["n"] == 1
        assert calls[0].json_body["response_format"] == "b64_json"
    else:
        assert calls[0].json_body["samples"] == 1
        assert "model" not in calls[0].json_body
    assert result.artifacts[0].artifact_id == "artifact-generated"


@pytest.mark.parametrize(
    "payload",
    [
        {"artifacts": [{"base64": "not-base64", "finishReason": "SUCCESS"}]},
        {"artifacts": [{"base64": PNG_BASE64, "finishReason": "CONTENT_FILTERED"}]},
        {"artifacts": [{"base64": base64.b64encode(b"not-an-image").decode("ascii")}]},
        {"data": []},
    ],
)
def test_invalid_or_filtered_provider_image_never_persists(payload: dict[str, object]) -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageGenerationRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimVisualProvider,
    )

    artifacts = MemoryImageArtifactStore()

    def transport(_request: ProviderHttpRequest) -> ProviderHttpResponse:
        return ProviderHttpResponse(statusCode=200, jsonBody=payload)

    provider = NvidiaNimVisualProvider(
        provider_id="nvidia-invalid-image",
        base_url="http://127.0.0.1:8103/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="image_generation",
        adapter_profile="nvidia_qwen_image_generation_infer",
        transport=transport,
        image_artifact_store=artifacts,
    )

    with pytest.raises(NvidiaNimCapabilityError, match="provider_response_invalid"):
        provider.generate_image(
            ImageGenerationRequest(
                projectId="project-invalid",
                model="qwen/qwen-image",
                prompt="A valid prompt",
            )
        )
    assert artifacts.persisted == []


def test_durable_image_store_enforces_project_ownership_root_hash_and_media(tmp_path: Path) -> None:
    from local_control_center.agents.providers.image_artifacts import (
        DurableImageArtifactStore,
        ImageArtifactError,
    )
    from local_control_center.shared.migrations import initialize_platform_schema

    database = tmp_path / "platform.sqlite"
    with open_sqlite_connection(database) as connection:
        initialize_platform_schema(connection)
        first = ProjectsRepository(connection).create_project(name="first", path=tmp_path / "first")
        second = ProjectsRepository(connection).create_project(name="second", path=tmp_path / "second")
        store = DurableImageArtifactStore(connection, root=tmp_path)

        reference = store.persist_image(
            project_id=str(first["id"]),
            provider_id="nvidia-images",
            model="qwen/qwen-image",
            content=PNG_BYTES,
        )

        assert reference.mime_type == "image/png"
        data_url = store.read_image_data_url(
            project_id=str(first["id"]),
            artifact_id=reference.artifact_id,
        )
        assert data_url == f"data:image/png;base64,{PNG_BASE64}"
        with pytest.raises(ImageArtifactError, match="image_artifact_not_found"):
            store.read_image_data_url(
                project_id=str(second["id"]),
                artifact_id=reference.artifact_id,
            )

        row = EvidenceRepository(connection).get_artifact_by_id(reference.artifact_id)
        Path(row["path"]).write_bytes(_png(width=2, height=2))
        with pytest.raises(ImageArtifactError, match="image_artifact_hash_mismatch"):
            store.read_image_data_url(
                project_id=str(first["id"]),
                artifact_id=reference.artifact_id,
            )


def test_durable_image_store_rejects_missing_project_invalid_media_and_oversized_dimensions(
    tmp_path: Path,
) -> None:
    from local_control_center.agents.providers.image_artifacts import (
        DurableImageArtifactStore,
        ImageArtifactError,
    )
    from local_control_center.shared.migrations import initialize_platform_schema

    database = tmp_path / "platform.sqlite"
    with open_sqlite_connection(database) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(name="images", path=tmp_path / "images")
        store = DurableImageArtifactStore(connection, root=tmp_path)

        with pytest.raises(ImageArtifactError, match="project_not_found"):
            store.persist_image(
                project_id="missing-project",
                provider_id="nvidia-images",
                model="qwen/qwen-image",
                content=PNG_BYTES,
            )
        with pytest.raises(ImageArtifactError, match="image_media_invalid"):
            store.persist_image(
                project_id=str(project["id"]),
                provider_id="nvidia-images",
                model="qwen/qwen-image",
                content=b"not-an-image",
            )
        with pytest.raises(ImageArtifactError, match="image_dimensions_invalid"):
            store.persist_image(
                project_id=str(project["id"]),
                provider_id="nvidia-images",
                model="qwen/qwen-image",
                content=_png(width=8193, height=1),
            )


def test_visual_api_persists_only_artifact_reference_and_downloads_by_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"data": [{"b64_json": PNG_BASE64}]},
        )

    monkeypatch.setattr(
        "local_control_center.agents.providers.nvidia_nim._stdlib_transport",
        transport,
    )
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    database = Path(os.environ["LOCAL_CONTROL_CENTER_DB"])
    project = _create_project(database, tmp_path, name="visual-project")
    other = _create_project(database, tmp_path, name="other-project")
    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-visual-local",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "self_hosted_development",
            "apiFamily": "image_generation",
            "adapterProfile": "nvidia_openai_image_generation",
            "termsMode": "accepted",
            "pricingMode": "free",
            "baseUrl": "http://127.0.0.1:8104/v1",
            "enabled": True,
        },
    )
    _enable_nvidia_policy(database)
    with open_sqlite_connection(database) as connection:
        ProviderAccountStore(connection).upsert_model(
            {
                "providerId": "nvidia-visual-local",
                "model": "qwen/qwen-image-2512",
                "apiFamily": "image_generation",
                "supportsImageGeneration": True,
                "enabled": True,
                "source": "operator_manifest",
            }
        )

    executed = client.post(
        "/api/v1/model-gateway/providers/nvidia-visual-local/images/generations",
        headers=headers,
        json={
            "projectId": project["id"],
            "model": "qwen/qwen-image-2512",
            "prompt": "AUDIT-PROMPT-MUST-STAY-PRIVATE",
        },
    )

    assert created.status_code == 201, created.text
    assert executed.status_code == 200, executed.text
    body = executed.json()["imageGeneration"]
    assert PNG_BASE64 not in executed.text
    assert ".tmp" not in str(body).lower()
    assert "evidence-artifacts" not in str(body).lower()
    artifact = body["artifacts"][0]
    downloaded = client.get(artifact["downloadPath"], headers=headers)
    wrong_project = client.get(
        f"/api/v1/projects/{other['id']}/artifacts/{artifact['artifactId']}",
        headers=headers,
    )
    unauthenticated = client.get(artifact["downloadPath"])
    with open_sqlite_connection(database) as connection:
        stored_artifact = EvidenceRepository(connection).get_artifact_by_id(artifact["artifactId"])
        audit_row = connection.execute(
            "SELECT payload FROM audit_events WHERE action = ? ORDER BY rowid DESC LIMIT 1",
            ("model_gateway.provider.image_generated",),
        ).fetchone()
    private_surfaces = f"{stored_artifact['metadata']} {audit_row['payload']}"

    assert calls[0].url == "http://127.0.0.1:8104/v1/images/generations"
    assert downloaded.status_code == 200
    assert downloaded.content == PNG_BYTES
    assert downloaded.headers["content-type"].startswith("image/png")
    assert wrong_project.status_code == 404
    assert unauthenticated.status_code == 403
    assert "AUDIT-PROMPT-MUST-STAY-PRIVATE" not in private_surfaces
    assert PNG_BASE64 not in private_surfaces
    assert "data:image" not in private_surfaces


@pytest.mark.parametrize(
    ("api_family", "profile", "deployment_mode"),
    [
        ("image_editing", "nvidia_qwen_image_editing_infer", "hosted_trial"),
        ("image_generation", "nvidia_openai_image_generation", "hosted_trial"),
        ("image_generation", "nvidia_hosted_prompt_image_generation", "self_hosted_development"),
        ("image_generation", "nvidia_hosted_prompt_image_generation", "custom"),
    ],
)
def test_visual_profile_deployment_mismatch_fails_before_transport(
    api_family: str,
    profile: str,
    deployment_mode: str,
    tmp_path: Path,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.factory import (
        ProviderAdapterFactory,
        UnsupportedAdapterProfileError,
    )
    from local_control_center.shared.migrations import initialize_platform_schema

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=500, jsonBody={})

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": f"nvidia-mismatch-{profile}",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": deployment_mode,
                "apiFamily": api_family,
                "adapterProfile": profile,
                "termsMode": "evaluation" if deployment_mode == "hosted_trial" else "accepted",
                "pricingMode": "unknown" if deployment_mode == "hosted_trial" else "free",
                "baseUrl": (
                    "http://127.0.0.1:8105/v1"
                    if deployment_mode.startswith("self_hosted")
                    else "https://ai.api.nvidia.com/v1/genai/qwen/qwen-image"
                ),
                "credentialRef": "env:NVIDIA_UNUSED_VISUAL_KEY"
                if deployment_mode == "hosted_trial"
                else "",
                "enabled": True,
            }
        )
        factory = ProviderAdapterFactory(
            connection,
            transport=transport,
            image_artifact_store=MemoryImageArtifactStore(),
        )
        with pytest.raises(UnsupportedAdapterProfileError, match="unsupported_adapter_profile"):
            factory.resolve_for_execution(f"nvidia-mismatch-{profile}")
    assert calls == []


def test_generation_requires_artifact_store_and_existing_project_before_transport() -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageGenerationRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.image_artifacts import ImageArtifactError
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimVisualProvider,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=200, jsonBody={})

    request = ImageGenerationRequest(
        projectId="missing-project",
        model="qwen/qwen-image",
        prompt="A safe prompt",
    )
    without_store = NvidiaNimVisualProvider(
        provider_id="nvidia-no-store",
        base_url="http://127.0.0.1:8116/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="image_generation",
        adapter_profile="nvidia_qwen_image_generation_infer",
        transport=transport,
    )
    with pytest.raises(NvidiaNimCapabilityError, match="image_artifact_store_required"):
        without_store.generate_image(request)

    class MissingProjectStore(MemoryImageArtifactStore):
        def require_project(self, *, project_id: str) -> None:
            raise ImageArtifactError("project_not_found")

    missing_project = NvidiaNimVisualProvider(
        provider_id="nvidia-missing-project",
        base_url="http://127.0.0.1:8117/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="image_generation",
        adapter_profile="nvidia_qwen_image_generation_infer",
        transport=transport,
        image_artifact_store=MissingProjectStore(),
    )
    with pytest.raises(ImageArtifactError, match="project_not_found"):
        missing_project.generate_image(request)
    assert calls == []


def test_hosted_model_must_match_model_specific_endpoint_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageGenerationRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimVisualProvider,
    )

    monkeypatch.setenv("NVIDIA_HOSTED_MODEL_KEY", "secret")
    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=200, jsonBody={})

    provider = NvidiaNimVisualProvider(
        provider_id="nvidia-hosted-model",
        base_url="https://ai.api.nvidia.com/v1/genai/qwen/qwen-image",
        credential_ref="env:NVIDIA_HOSTED_MODEL_KEY",
        deployment_mode="hosted_trial",
        api_family="image_generation",
        adapter_profile="nvidia_hosted_prompt_image_generation",
        transport=transport,
        image_artifact_store=MemoryImageArtifactStore(),
    )

    with pytest.raises(NvidiaNimCapabilityError, match="model_endpoint_mismatch"):
        provider.generate_image(
            ImageGenerationRequest(
                projectId="project-hosted",
                model="black-forest-labs/flux.1-dev",
                prompt="A safe prompt",
            )
        )
    assert calls == []


def test_openai_visual_profile_rejects_aspect_ratio_before_transport() -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageGenerationRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimVisualProvider,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=200, jsonBody={})

    provider = NvidiaNimVisualProvider(
        provider_id="nvidia-openai-aspect",
        base_url="http://127.0.0.1:8110/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="image_generation",
        adapter_profile="nvidia_openai_image_generation",
        transport=transport,
        image_artifact_store=MemoryImageArtifactStore(),
    )

    with pytest.raises(NvidiaNimCapabilityError, match="unsupported_image_option"):
        provider.generate_image(
            ImageGenerationRequest(
                projectId="project-aspect",
                model="qwen/qwen-image",
                prompt="A safe prompt",
                aspectRatio="1:1",
            )
        )
    assert calls == []


@pytest.mark.parametrize(
    "response",
    [
        {"artifacts": [{"base64": PNG_BASE64}]},
        {"artifacts": [{"base64": PNG_BASE64, "finishReason": "ERROR"}]},
        {
            "artifacts": [
                {"base64": PNG_BASE64, "finishReason": "SUCCESS"},
                {"base64": PNG_BASE64, "finishReason": "SUCCESS"},
            ]
        },
    ],
)
def test_native_visual_response_requires_exactly_one_success_artifact(
    response: dict[str, object],
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageGenerationRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimVisualProvider,
    )

    artifacts = MemoryImageArtifactStore()

    def transport(_request: ProviderHttpRequest) -> ProviderHttpResponse:
        return ProviderHttpResponse(statusCode=200, jsonBody=response)

    provider = NvidiaNimVisualProvider(
        provider_id="nvidia-native-strict",
        base_url="http://127.0.0.1:8111/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="image_generation",
        adapter_profile="nvidia_qwen_image_generation_infer",
        transport=transport,
        image_artifact_store=artifacts,
    )

    with pytest.raises(NvidiaNimCapabilityError, match="provider_response_invalid"):
        provider.generate_image(
            ImageGenerationRequest(
                projectId="project-strict",
                model="qwen/qwen-image",
                prompt="A safe prompt",
            )
        )
    assert artifacts.persisted == []


def test_provider_base64_limit_is_checked_before_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageGenerationRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimVisualProvider,
    )

    monkeypatch.setattr("local_control_center.agents.providers.nvidia_nim.MAX_IMAGE_BASE64_CHARS", 16)
    artifacts = MemoryImageArtifactStore()

    def transport(_request: ProviderHttpRequest) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"artifacts": [{"base64": PNG_BASE64, "finishReason": "SUCCESS"}]},
        )

    provider = NvidiaNimVisualProvider(
        provider_id="nvidia-encoded-limit",
        base_url="http://127.0.0.1:8112/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="image_generation",
        adapter_profile="nvidia_qwen_image_generation_infer",
        transport=transport,
        image_artifact_store=artifacts,
    )

    with pytest.raises(NvidiaNimCapabilityError, match="provider_response_invalid"):
        provider.generate_image(
            ImageGenerationRequest(
                projectId="project-limit",
                model="qwen/qwen-image",
                prompt="A safe prompt",
            )
        )
    assert artifacts.persisted == []


def test_input_artifact_conflicts_are_detected_before_provider_transport(tmp_path: Path) -> None:
    from local_control_center.agents.providers.capabilities import (
        ImageEditingRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.image_artifacts import DurableImageArtifactStore
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimVisualProvider
    from local_control_center.shared.migrations import initialize_platform_schema

    database = tmp_path / "platform.sqlite"
    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=200, jsonBody={})

    with open_sqlite_connection(database) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(name="inputs", path=tmp_path / "inputs")
        outside = tmp_path / "outside.png"
        outside.write_bytes(PNG_BYTES)
        artifact = EvidenceRepository(connection).create_artifact(
            project_id=str(project["id"]),
            evidence_package_id=None,
            kind="generated_image",
            path=str(outside),
            content_hash=hashlib.sha256(PNG_BYTES).hexdigest(),
            metadata={"mimeType": "image/png", "sizeBytes": len(PNG_BYTES)},
        )
        provider = NvidiaNimVisualProvider(
            provider_id="nvidia-edit-boundary",
            base_url="http://127.0.0.1:8113/v1",
            credential_ref="",
            deployment_mode="self_hosted_development",
            api_family="image_editing",
            adapter_profile="nvidia_qwen_image_editing_infer",
            transport=transport,
            image_artifact_store=DurableImageArtifactStore(connection, root=tmp_path),
        )

        with pytest.raises(RuntimeError, match="image_artifact_path_forbidden"):
            provider.edit_image(
                ImageEditingRequest(
                    projectId=project["id"],
                    model="qwen/qwen-image-edit",
                    prompt="A safe edit",
                    inputArtifactId=artifact["id"],
                )
            )
    assert calls == []


def test_input_artifact_size_is_checked_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.image_artifacts import (
        DurableImageArtifactStore,
        ImageArtifactError,
    )
    from local_control_center.evidence.artifacts import write_binary_artifact
    from local_control_center.evidence.image_validation import MAX_IMAGE_BYTES
    from local_control_center.shared.migrations import initialize_platform_schema

    database = tmp_path / "platform.sqlite"
    with open_sqlite_connection(database) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(name="large", path=tmp_path / "large")
        artifact_file = write_binary_artifact(
            root=tmp_path,
            artifact_id="artifact-large",
            suffix=".png",
            content=PNG_BYTES,
        )
        path = Path(artifact_file["path"])
        path.write_bytes(PNG_BYTES + (b"x" * (MAX_IMAGE_BYTES + 1)))
        artifact = EvidenceRepository(connection).create_artifact(
            project_id=str(project["id"]),
            evidence_package_id=None,
            kind="generated_image",
            path=str(path),
            content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            metadata={"mimeType": "image/png", "sizeBytes": path.stat().st_size},
        )
        monkeypatch.setattr(Path, "read_bytes", lambda _path: pytest.fail("oversized file was read"))
        with pytest.raises(ImageArtifactError, match="image_size_invalid"):
            DurableImageArtifactStore(connection, root=tmp_path).read_image_data_url(
                project_id=str(project["id"]),
                artifact_id=str(artifact["id"]),
            )


def test_artifact_insert_failure_removes_only_new_output_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.image_artifacts import DurableImageArtifactStore
    from local_control_center.shared.migrations import initialize_platform_schema

    database = tmp_path / "platform.sqlite"
    with open_sqlite_connection(database) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(name="rollback", path=tmp_path / "rollback")
        store = DurableImageArtifactStore(connection, root=tmp_path)
        artifact_root = tmp_path / ".tmp" / "evidence-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        existing = artifact_root / "existing.png"
        existing.write_bytes(PNG_BYTES)
        monkeypatch.setattr(
            store.repository,
            "create_artifact",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("insert failed")),
        )

        with pytest.raises(RuntimeError, match="insert failed"):
            store.persist_image(
                project_id=str(project["id"]),
                provider_id="nvidia-rollback",
                model="qwen/qwen-image",
                content=PNG_BYTES,
            )

        assert list(artifact_root.iterdir()) == [existing]


def test_visual_manifest_and_discovery_fail_closed_without_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=200, jsonBody={})

    monkeypatch.setattr("local_control_center.agents.providers.nvidia_nim._stdlib_transport", transport)
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    database = Path(os.environ["LOCAL_CONTROL_CENTER_DB"])
    project = _create_project(database, tmp_path, name="manifest-project")
    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-visual-manifest",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "self_hosted_development",
            "apiFamily": "image_generation",
            "adapterProfile": "nvidia_openai_image_generation",
            "termsMode": "accepted",
            "pricingMode": "free",
            "baseUrl": "http://127.0.0.1:8114/v1",
            "enabled": True,
        },
    )
    _enable_nvidia_policy(database)
    missing = client.post(
        "/api/v1/model-gateway/providers/nvidia-visual-manifest/images/generations",
        headers=headers,
        json={"projectId": project["id"], "model": "qwen/image", "prompt": "safe"},
    )
    discovery = client.post(
        "/api/v1/model-gateway/providers/nvidia-visual-manifest/discover-models",
        headers=headers,
    )
    sync = client.post(
        "/api/v1/provider-accounts/nvidia-visual-manifest/sync-models",
        headers=headers,
    )
    with open_sqlite_connection(database) as connection:
        ProviderAccountStore(connection).upsert_model(
            {
                "providerId": "nvidia-visual-manifest",
                "model": "qwen/image",
                "apiFamily": "image_generation",
                "supportsImageGeneration": False,
                "enabled": True,
                "source": "operator_manifest",
            }
        )
    capability_false = client.post(
        "/api/v1/model-gateway/providers/nvidia-visual-manifest/images/generations",
        headers=headers,
        json={"projectId": project["id"], "model": "qwen/image", "prompt": "safe"},
    )
    with open_sqlite_connection(database) as connection:
        ProviderAccountStore(connection).upsert_model(
            {
                "providerId": "nvidia-visual-manifest",
                "model": "qwen/image",
                "apiFamily": "image_generation",
                "supportsImageGeneration": True,
                "enabled": False,
                "source": "operator_manifest",
            }
        )
    disabled = client.post(
        "/api/v1/model-gateway/providers/nvidia-visual-manifest/images/generations",
        headers=headers,
        json={"projectId": project["id"], "model": "qwen/image", "prompt": "safe"},
    )
    with open_sqlite_connection(database) as connection:
        ProviderAccountStore(connection).upsert_model(
            {
                "providerId": "nvidia-visual-manifest",
                "model": "qwen/image",
                "apiFamily": "rerank",
                "supportsImageGeneration": True,
                "enabled": True,
                "source": "operator_manifest",
            }
        )
    family_mismatch = client.post(
        "/api/v1/model-gateway/providers/nvidia-visual-manifest/images/generations",
        headers=headers,
        json={"projectId": project["id"], "model": "qwen/image", "prompt": "safe"},
    )

    assert created.status_code == 201
    assert (missing.status_code, missing.json()["detail"]) == (409, "model_manifest_required")
    assert (discovery.status_code, discovery.json()["detail"]) == (
        409,
        "explicit_model_manifest_required",
    )
    assert (sync.status_code, sync.json()["detail"]) == (
        409,
        "explicit_model_manifest_required",
    )
    assert (capability_false.status_code, capability_false.json()["detail"]) == (
        409,
        "model_capability_mismatch",
    )
    assert (disabled.status_code, disabled.json()["detail"]) == (409, "model_disabled")
    assert (family_mismatch.status_code, family_mismatch.json()["detail"]) == (
        409,
        "model_api_family_mismatch",
    )
    assert calls == []


def test_visual_validation_error_does_not_echo_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _create_client(tmp_path, monkeypatch)
    marker = "PROMPT-MUST-NOT-ECHO"
    response = client.post(
        "/api/v1/model-gateway/providers/missing/images/generations",
        headers=_auth_headers(client),
        json={
            "projectId": "project-validation",
            "model": "qwen/image",
            "prompt": marker * 1_000,
        },
    )

    assert response.status_code == 422
    assert marker not in response.text


def test_hosted_visual_health_is_passive_and_does_not_require_artifact_store() -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimVisualProvider

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=500, jsonBody={})

    provider = NvidiaNimVisualProvider(
        provider_id="nvidia-hosted-health",
        base_url="https://ai.api.nvidia.com/v1/genai/qwen/qwen-image",
        credential_ref="env:NVIDIA_NOT_RESOLVED_FOR_PASSIVE_HEALTH",
        deployment_mode="hosted_trial",
        api_family="image_generation",
        adapter_profile="nvidia_hosted_prompt_image_generation",
        transport=transport,
    )

    health = provider.health_check()

    assert health.status == "configured"
    assert health.health_status == "unknown"
    assert calls == []


def test_hosted_visual_base_url_rejects_prefixed_genai_path() -> None:
    from local_control_center.agents.provider_accounts import validate_provider_base_url

    with pytest.raises(ValueError, match="model-specific"):
        validate_provider_base_url(
            "https://ai.api.nvidia.com/proxy/v1/genai/qwen/qwen-image",
            provider_family="nvidia_nim",
            deployment_mode="hosted_trial",
            api_family="image_generation",
            adapter_profile="nvidia_hosted_prompt_image_generation",
        )


def test_project_image_download_rejects_tampering_external_paths_missing_hash_and_false_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.evidence.artifacts import write_binary_artifact

    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    database = Path(os.environ["LOCAL_CONTROL_CENTER_DB"])
    project = _create_project(database, tmp_path, name="downloads")
    project_id = str(project["id"])
    outside = tmp_path / "outside-download.png"
    outside.write_bytes(PNG_BYTES)

    with open_sqlite_connection(database) as connection:
        repository = EvidenceRepository(connection)

        tampered_file = write_binary_artifact(
            root=tmp_path,
            artifact_id="artifact-tampered",
            suffix=".png",
            content=PNG_BYTES,
        )
        repository.create_artifact(
            project_id=project_id,
            evidence_package_id=None,
            kind="generated_image",
            path=str(tampered_file["path"]),
            content_hash="0" * 64,
            metadata={"mimeType": "image/png", "sizeBytes": len(PNG_BYTES)},
            artifact_id="artifact-tampered",
        )
        repository.create_artifact(
            project_id=project_id,
            evidence_package_id=None,
            kind="generated_image",
            path=str(outside),
            content_hash=hashlib.sha256(PNG_BYTES).hexdigest(),
            metadata={"mimeType": "image/png", "sizeBytes": len(PNG_BYTES)},
            artifact_id="artifact-outside",
        )
        no_hash_file = write_binary_artifact(
            root=tmp_path,
            artifact_id="artifact-no-hash",
            suffix=".png",
            content=PNG_BYTES,
        )
        repository.create_artifact(
            project_id=project_id,
            evidence_package_id=None,
            kind="generated_image",
            path=str(no_hash_file["path"]),
            content_hash=None,
            metadata={"mimeType": "image/png", "sizeBytes": len(PNG_BYTES)},
            artifact_id="artifact-no-hash",
        )
        false_media_file = write_binary_artifact(
            root=tmp_path,
            artifact_id="artifact-false-media",
            suffix=".png",
            content=PNG_BYTES,
        )
        repository.create_artifact(
            project_id=project_id,
            evidence_package_id=None,
            kind="generated_image",
            path=str(false_media_file["path"]),
            content_hash=hashlib.sha256(PNG_BYTES).hexdigest(),
            metadata={"mimeType": "text/plain", "sizeBytes": len(PNG_BYTES)},
            artifact_id="artifact-false-media",
        )
        missing_path = tmp_path / ".tmp" / "evidence-artifacts" / "missing.png"
        repository.create_artifact(
            project_id=project_id,
            evidence_package_id=None,
            kind="generated_image",
            path=str(missing_path),
            content_hash=hashlib.sha256(PNG_BYTES).hexdigest(),
            metadata={"mimeType": "image/png", "sizeBytes": len(PNG_BYTES)},
            artifact_id="artifact-missing",
        )
        truncated_file = write_binary_artifact(
            root=tmp_path,
            artifact_id="artifact-truncated",
            suffix=".png",
            content=PNG_BYTES[:8],
        )
        repository.create_artifact(
            project_id=project_id,
            evidence_package_id=None,
            kind="generated_image",
            path=str(truncated_file["path"]),
            content_hash=hashlib.sha256(PNG_BYTES[:8]).hexdigest(),
            metadata={"mimeType": "image/png", "sizeBytes": 8},
            artifact_id="artifact-truncated",
        )

    responses = {
        artifact_id: client.get(
            f"/api/v1/projects/{project_id}/artifacts/{artifact_id}",
            headers=headers,
        )
        for artifact_id in (
            "artifact-tampered",
            "artifact-outside",
            "artifact-no-hash",
            "artifact-false-media",
            "artifact-missing",
            "artifact-truncated",
        )
    }

    assert responses["artifact-tampered"].status_code == 409
    assert responses["artifact-outside"].status_code == 403
    assert responses["artifact-no-hash"].status_code == 409
    assert responses["artifact-false-media"].status_code == 409
    assert responses["artifact-missing"].status_code == 404
    assert responses["artifact-truncated"].status_code == 409


def test_visual_edit_api_uses_project_input_and_returns_only_output_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.image_artifacts import DurableImageArtifactStore

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"data": [{"b64_json": PNG_BASE64}]},
        )

    monkeypatch.setattr("local_control_center.agents.providers.nvidia_nim._stdlib_transport", transport)
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    database = Path(os.environ["LOCAL_CONTROL_CENTER_DB"])
    project = _create_project(database, tmp_path, name="edit-api")
    project_id = str(project["id"])
    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-visual-edit",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "self_hosted_development",
            "apiFamily": "image_editing",
            "adapterProfile": "nvidia_openai_image_editing",
            "termsMode": "accepted",
            "pricingMode": "free",
            "baseUrl": "http://127.0.0.1:8115/v1",
            "enabled": True,
        },
    )
    _enable_nvidia_policy(database)
    with open_sqlite_connection(database) as connection:
        input_artifact = DurableImageArtifactStore(connection, root=tmp_path).persist_image(
            project_id=project_id,
            provider_id="test-input",
            model="test-input",
            content=PNG_BYTES,
        )
        ProviderAccountStore(connection).upsert_model(
            {
                "providerId": "nvidia-visual-edit",
                "model": "qwen/qwen-image-edit-2511",
                "apiFamily": "image_editing",
                "supportsImageEditing": True,
                "enabled": True,
                "source": "operator_manifest",
            }
        )

    executed = client.post(
        "/api/v1/model-gateway/providers/nvidia-visual-edit/images/edits",
        headers=headers,
        json={
            "projectId": project_id,
            "model": "qwen/qwen-image-edit-2511",
            "prompt": "EDIT-PROMPT-MUST-STAY-PRIVATE",
            "inputArtifactId": input_artifact.artifact_id,
        },
    )

    assert created.status_code == 201
    assert executed.status_code == 200, executed.text
    assert calls[0].url == "http://127.0.0.1:8115/v1/images/edits"
    assert calls[0].json_body["image"].startswith("data:image/png;base64,")
    assert input_artifact.artifact_id not in executed.text
    assert "EDIT-PROMPT-MUST-STAY-PRIVATE" not in executed.text
    assert PNG_BASE64 not in executed.text
    assert executed.json()["imageEditing"]["artifacts"][0]["artifactId"] != input_artifact.artifact_id
