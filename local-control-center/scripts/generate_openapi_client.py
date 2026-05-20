from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "local-control-center" / "web" / "src" / "api" / "generated" / "openapi.ts"


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _endpoint_rows(openapi: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path, methods in sorted(openapi.get("paths", {}).items()):
        if not path.startswith("/api/v1/") and path != "/healthz":
            continue
        for method, spec in sorted(methods.items()):
            if method.lower() not in {"get", "post", "patch", "put", "delete"}:
                continue
            rows.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operationId": str(spec.get("operationId") or ""),
                    "summary": str(spec.get("summary") or ""),
                }
            )
    return rows


def _json_schema_for_response(spec: dict[str, Any]) -> dict[str, Any] | None:
    responses = spec.get("responses", {})
    for status in ("200", "201", "202", "204", "default"):
        schema = responses.get(status, {}).get("content", {}).get("application/json", {}).get("schema")
        if schema:
            return schema
    return None


def _json_schema_for_request(spec: dict[str, Any]) -> dict[str, Any] | None:
    return spec.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")


def _ts_type_from_schema(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "never"
    if "$ref" in schema:
        return "JsonObject"
    if "anyOf" in schema:
        return " | ".join(sorted({_ts_type_from_schema(item) for item in schema["anyOf"]}))
    if "oneOf" in schema:
        return " | ".join(sorted({_ts_type_from_schema(item) for item in schema["oneOf"]}))
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(sorted({_ts_type_from_schema({**schema, "type": item}) for item in schema_type}))
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "array":
        return f"Array<{_ts_type_from_schema(schema.get('items') or {})}>"
    if schema_type == "object" or schema.get("additionalProperties") is not None or schema.get("properties"):
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        if not properties:
            return "JsonObject"
        fields = []
        for name, value in sorted(properties.items()):
            optional = "" if name in required else "?"
            fields.append(f"{_literal(name)}{optional}: {_ts_type_from_schema(value)}")
        return "{ " + "; ".join(fields) + " }"
    return "JsonValue"


def _operation_schema_maps(openapi: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    request_bodies: dict[str, str] = {}
    response_bodies: dict[str, str] = {}
    for path, methods in sorted(openapi.get("paths", {}).items()):
        if not path.startswith("/api/v1/") and path != "/healthz":
            continue
        for method, spec in sorted(methods.items()):
            if method.lower() not in {"get", "post", "patch", "put", "delete"}:
                continue
            operation_id = str(spec.get("operationId") or "")
            if not operation_id:
                continue
            request_schema = _json_schema_for_request(spec)
            request_bodies[operation_id] = (
                _ts_type_from_schema(request_schema)
                if request_schema
                else ("unknown" if method.lower() in {"post", "patch", "put"} else "never")
            )
            response_bodies[operation_id] = _ts_type_from_schema(_json_schema_for_response(spec))
    return request_bodies, response_bodies


def render_client(openapi: dict[str, Any]) -> str:
    endpoints = _endpoint_rows(openapi)
    request_bodies, response_bodies = _operation_schema_maps(openapi)
    endpoint_lines = ",\n".join(f"\t{_literal(endpoint)}" for endpoint in endpoints)
    operation_lines = ",\n".join(
        f"\t{_literal(endpoint['operationId'])}: {_literal(endpoint)}" for endpoint in endpoints if endpoint["operationId"]
    )
    request_body_lines = ",\n".join(
        f"\t{_literal(operation_id)}: {body}" for operation_id, body in sorted(request_bodies.items())
    )
    response_body_lines = ",\n".join(
        f"\t{_literal(operation_id)}: {body}" for operation_id, body in sorted(response_bodies.items())
    )
    return f"""// Generated from FastAPI OpenAPI. Do not edit by hand.
// No network access is required; run `corepack pnpm@10.24.0 run openapi:generate`.

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = {{ [key: string]: JsonValue }};

export const OPENAPI_TITLE = {_literal(openapi.get("info", {}).get("title", ""))} as const;
export const OPENAPI_VERSION = {_literal(openapi.get("info", {}).get("version", ""))} as const;

export const API_ENDPOINTS = [
{endpoint_lines}
] as const;

export type ApiEndpoint = (typeof API_ENDPOINTS)[number];
export type ApiMethod = ApiEndpoint["method"];
export type ApiPath = ApiEndpoint["path"];
export type ApiOperationId = ApiEndpoint["operationId"];
export type OperationById<T extends ApiOperationId> = Extract<ApiEndpoint, {{ operationId: T }}>;
export type OperationPath<T extends ApiOperationId> = OperationById<T>["path"];
export type OperationMethod<T extends ApiOperationId> = OperationById<T>["method"];

export type OperationRequestBodies = {{
{request_body_lines}
}};

export type OperationResponseBodies = {{
{response_body_lines}
}};

export type OperationRequestBody<T extends ApiOperationId> = OperationRequestBodies[T];
export type OperationResponse<T extends ApiOperationId> = OperationResponseBodies[T];

export const OPERATIONS_BY_ID = {{
{operation_lines}
}} as const satisfies Record<ApiOperationId, ApiEndpoint>;

export type GeneratedRequestOptions<TBody = unknown> = {{
\tpathParams?: Record<string, string | number>;
\tquery?: Record<string, string | number | boolean | null | undefined>;
\ttoken?: string;
\tbody?: TBody;
\tsignal?: AbortSignal;
}};

export function findEndpoint(method: ApiMethod, path: ApiPath): ApiEndpoint | undefined {{
\treturn API_ENDPOINTS.find((endpoint) => endpoint.method === method && endpoint.path === path);
}}

export function buildApiPath(
\tpath: string,
\tpathParams: Record<string, string | number> = {{}},
\tquery: Record<string, string | number | boolean | null | undefined> = {{}},
): string {{
\tconst resolvedPath = path.replace(/\\{{([^}}]+)\\}}/g, (_match, key: string) => {{
\t\tconst value = pathParams[key];
\t\tif (value === undefined || value === null) {{
\t\t\tthrow new Error(`Missing path parameter: ${{key}}`);
\t\t}}
\t\treturn encodeURIComponent(String(value));
\t}});
\tconst params = new URLSearchParams();
\tfor (const [key, value] of Object.entries(query)) {{
\t\tif (value !== undefined && value !== null) params.set(key, String(value));
\t}}
\tconst queryString = params.toString();
\treturn queryString ? `${{resolvedPath}}?${{queryString}}` : resolvedPath;
}}

export async function requestGeneratedOperation<
\tTOperationId extends ApiOperationId,
\tTResponse = OperationResponse<TOperationId>,
>(
\toperationId: TOperationId,
\toptions: GeneratedRequestOptions<OperationRequestBody<TOperationId>> = {{}},
): Promise<TResponse> {{
\tconst endpoint = OPERATIONS_BY_ID[operationId];
\tconst headers: Record<string, string> = {{ Accept: "application/json" }};
\tif (options.body !== undefined) headers["Content-Type"] = "application/json";
\tif (options.token) headers["X-Local-Control-Token"] = options.token;
\tconst response = await fetch(buildApiPath(endpoint.path, options.pathParams, options.query), {{
\t\tmethod: endpoint.method,
\t\theaders,
\t\tbody: options.body === undefined ? undefined : JSON.stringify(options.body),
\t\tsignal: options.signal,
\t}});
\tconst text = await response.text();
\tconst payload = text ? JSON.parse(text) : {{}};
\tif (!response.ok) {{
\t\tconst detail = payload.detail ?? payload.error ?? response.statusText;
\t\tthrow new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
\t}}
\treturn payload as TResponse;
}}
"""


def main() -> None:
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    with tempfile.TemporaryDirectory(prefix="aido-openapi-") as tmp:
        runtime = ControlCenterRuntime(cwd=ROOT, db_path=Path(tmp) / "platform.sqlite")
        app = create_app(runtime=runtime, static_dir=None)
        try:
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT.write_text(render_client(app.openapi()), encoding="utf-8", newline="\n")
        finally:
            runtime.close()


if __name__ == "__main__":
    main()
