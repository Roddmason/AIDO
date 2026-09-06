"""CLI local de diagnóstico: capacidades, activación temporal y exportación sin memoria.

Activar no aprueba trabajo ni inicia inferencia. La ejecución sigue la ruta HTTP/worker normal.
@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from local_control_center.shared.diagnostics import (
    activate_attempt,
    diagnostic_root,
    export_incident,
    incident_events,
    native_capabilities,
)


def main() -> int:
    """Interfaz pública; stdout contiene únicamente JSON, sin logs de protocolo mezclados."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=diagnostic_root())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    enable = sub.add_parser("enable")
    enable.add_argument("--execution-id", required=True)
    enable.add_argument("--ttl-seconds", required=True, type=int)
    enable.add_argument("--native-collector")
    enable.add_argument("--executable-sha256")
    incident = sub.add_parser("incident")
    incident.add_argument("--execution-id", required=True)
    export = sub.add_parser("export")
    export.add_argument("--execution-id", required=True)
    export.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "capabilities":
            result = native_capabilities()
        elif args.command == "enable":
            result = activate_attempt(
                args.root,
                args.execution_id,
                ttl_seconds=args.ttl_seconds,
                native_collector=args.native_collector,
                executable_sha256=args.executable_sha256,
            )
        elif args.command == "export":
            result = export_incident(args.root, args.execution_id, args.output)
        else:
            result = {
                "executionId": args.execution_id,
                "events": list(incident_events(args.root, args.execution_id)),
            }
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "BLOCKED", "errorCode": type(error).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
