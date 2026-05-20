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

export function findEndpoint(method: ApiMethod, path: ApiPath): ApiEndpoint | undefined {{
\treturn API_ENDPOINTS.find((endpoint) => endpoint.method === method && endpoint.path === path);
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
