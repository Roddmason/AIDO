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


def render_client(openapi: dict[str, Any]) -> str:
    endpoints = _endpoint_rows(openapi)
    endpoint_lines = ",\n".join(f"\t{_literal(endpoint)}" for endpoint in endpoints)
    operation_lines = ",\n".join(
        f"\t{_literal(endpoint['operationId'])}: {_literal(endpoint)}" for endpoint in endpoints if endpoint["operationId"]
    )
    return f"""// Generated from FastAPI OpenAPI. Do not edit by hand.
// No network access is required; run `corepack pnpm@10.24.0 run openapi:generate`.

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

export const OPERATIONS_BY_ID = {{
{operation_lines}
}} as const satisfies Record<ApiOperationId, ApiEndpoint>;

export type GeneratedRequestOptions = {{
\tpathParams?: Record<string, string | number>;
\tquery?: Record<string, string | number | boolean | null | undefined>;
\ttoken?: string;
\tbody?: unknown;
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

export async function requestGeneratedOperation<TResponse = unknown>(
\toperationId: ApiOperationId,
\toptions: GeneratedRequestOptions = {{}},
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
