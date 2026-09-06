"""Canal JSONL local del logging estándar, independiente de la auditoría SQLite.

Un escritor por proceso; el presupuesto del directorio se coordina entre procesos.
Sólo administra sus propios segmentos diag-*.closed.jsonl, nunca evidencia histórica.
@author Rodrigo Mason
"""

from __future__ import annotations

import atexit
import errno
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .redaction import redact_secrets

MAX_EVENT_BYTES = 8192
GLOBAL_BYTES = 256 * 1024 * 1024
RUST_LOG_FILTER = "error,codex_exec=info"
_FIELDS = {
    "requestId",
    "executionId",
    "attemptId",
    "workerId",
    "fencingToken",
    "resourceLeaseId",
    "managedProcessId",
    "pid",
    "processCreationTime",
    "runtimeId",
    "runtimeVersion",
    "durationMs",
    "outcome",
    "errorCode",
    "causeStatus",
    "evidenceRefs",
    "phase",
    "operation",
    "connectionId",
    "transactionId",
    "waitDurationMs",
    "attempt",
    "deadlineRemainingMs",
    "reason",
    "statusCode",
    "method",
    "requested",
    "applied",
    "verified",
    "win32Error",
    "executableRequested",
    "executableResolved",
    "executableSha256",
    "commandFingerprint",
    "stdinMode",
    "stdinEof",
    "stdoutMode",
    "stderrMode",
    "encoding",
    "totalBytes",
    "sizeBytes",
    "truncated",
    "stream",
    "limits",
    "sample",
    "diagnosticsDegraded",
    "droppedEvents",
    "exceptionChain",
    "effectiveConfig",
    "debuggerPaused",
    "symbolsAvailable",
}
_IDENTITY = {"executionId", "requestId", "attemptId", "managedProcessId", "pid", "processCreationTime"}
_sink: DiagnosticHandler | None = None
_logger = logging.getLogger("aido.diagnostics")
_logger.propagate = False
_logger.setLevel(logging.DEBUG)


def diagnostic_root() -> Path:
    """Un presupuesto compartido por todas las instancias del usuario, no por DB/job."""
    return Path(
        os.environ.get("AIDO_DIAGNOSTICS_DIR")
        or (Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "AIDO/diagnostics")
    )


def clean(value):
    """Redacción antes de cruzar la cola; jamás serializa objetos mediante repr/default=str."""
    if isinstance(value, dict):
        return {
            str(k): (
                v if k == "fencingToken" and isinstance(v, int) else clean(redact_secrets(v, key=str(k)))
            )
            for k, v in list(value.items())[:80]
            if str(k).lower()
            not in {"prompt", "auth.json", "environment", "headers", "stdin", "input", "locals"}
        }
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value[:40]]
    if isinstance(value, str):
        return re.sub(
            r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret)\s*[=:]\s*[^\s,;]+",
            "[redacted]",
            str(redact_secrets(value)),
        )[: MAX_EVENT_BYTES * 2]
    return value if value is None or isinstance(value, (bool, int, float)) else "[unsupported]"


def exception_chain(error: BaseException) -> list[dict]:
    """Conserva causas y frames sin parámetros, SQL, entorno ni variables locales."""
    result, seen = [], set()
    while error is not None and id(error) not in seen and len(result) < 8:
        seen.add(id(error))
        frames, tb = [], error.__traceback__
        while tb and len(frames) < 12:
            frames.append(
                {
                    "file": Path(tb.tb_frame.f_code.co_filename).name,
                    "function": tb.tb_frame.f_code.co_name,
                    "line": tb.tb_lineno,
                }
            )
            tb = tb.tb_next
        result.append(
            clean(
                {
                    "type": type(error).__name__,
                    "message": str(error),
                    "sqlite_errorcode": getattr(error, "sqlite_errorcode", None),
                    "sqlite_errorname": getattr(error, "sqlite_errorname", None),
                    "winerror": getattr(error, "winerror", None),
                    "frames": frames,
                }
            )
        )
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return result


def context_fields(context=None) -> dict:
    """Proyecta exclusivamente identificadores del contexto existente."""
    from local_control_center.process_supervision.context import CURRENT_EXECUTION

    ctx = context or CURRENT_EXECUTION.get()
    if ctx is None:
        return {}
    return {
        key: value
        for key, value in {
            "requestId": ctx.request_id,
            "executionId": ctx.execution_id,
            "attemptId": ctx.attempt_id,
            "workerId": ctx.worker_id,
            "fencingToken": ctx.fencing_token,
            "resourceLeaseId": ctx.resource_lease_id,
        }.items()
        if value is not None
    }


@contextmanager
def budget_lock(root: Path):
    """Lock OS no bloqueante; sólo el escritor toca disco. No se usa desde requests."""
    with (root / ".diagnostic-budget.lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class DiagnosticHandler(logging.Handler):
    """Cola acotada por bytes y eventos; el productor nunca espera al disco."""

    def __init__(
        self,
        root: Path,
        *,
        queue_count=1024,
        queue_bytes=4 * 1024 * 1024,
        file_bytes=4 * 1024 * 1024,
        global_bytes=GLOBAL_BYTES,
    ):
        super().__init__()
        if not (
            1 <= queue_count <= 8192
            and MAX_EVENT_BYTES <= queue_bytes <= 64 * 1024 * 1024
            and MAX_EVENT_BYTES <= file_bytes <= global_bytes <= GLOBAL_BYTES
        ):
            raise ValueError("Invalid diagnostic byte/count budgets")
        self.root, self.queue_bytes, self.file_bytes, self.global_bytes = (
            Path(root),
            queue_bytes,
            file_bytes,
            global_bytes,
        )
        self.instance = f"{os.getpid()}-{uuid.uuid4().hex}"
        self.pending = queue.Queue(maxsize=queue_count)
        self.guard, self.stopping = threading.Lock(), threading.Event()
        self.queued_bytes = self.sequence = self.dropped = self.segment = 0
        self.reason = ""
        self.path = None
        self.writer = threading.Thread(target=self._drain, name="aido-diagnostics", daemon=True)
        self.writer.start()

    def status(self):
        """Estado consultable aun cuando el disco no está disponible."""
        return {
            "diagnosticsDegraded": bool(self.dropped),
            "droppedEvents": self.dropped,
            "reason": self.reason,
            "queueBytes": self.queued_bytes,
            "globalBudgetBytes": self.global_bytes,
        }

    def _drop(self, reason):
        self.dropped += 1
        self.reason = reason

    def handle(self, record):
        """Evita el lock bloqueante predeterminado de logging.Handler."""
        # logging.Handler.handle normally serializes emit with an unbounded lock.
        self.emit(record)
        return True

    def emit(self, record):
        """Serializa un evento saneado y lo admite sin esperar al escritor."""
        if self.stopping.is_set():
            self._drop("closed")
            return
        if not self.guard.acquire(blocking=False):
            self._drop("producer_contention")
            return
        try:
            self.sequence += 1
            event = {
                **record.diagnostic,
                "schemaVersion": 1,
                "timestampUtc": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "monotonicNs": time.monotonic_ns(),
                "sequence": self.sequence,
                "level": record.levelname,
                "processInstanceId": self.instance,
            }
            if self.dropped:
                event.update({k: v for k, v in self.status().items() if k in _FIELDS})
            data = (
                json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            if len(data) > MAX_EVENT_BYTES:
                for key in sorted(
                    set(event)
                    - _IDENTITY
                    - {
                        "event",
                        "component",
                        "schemaVersion",
                        "timestampUtc",
                        "monotonicNs",
                        "sequence",
                        "level",
                        "processInstanceId",
                    },
                    key=lambda k: len(json.dumps(event[k])),
                    reverse=True,
                ):
                    event.pop(key)
                    event["truncated"] = True
                    data = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                        "utf-8"
                    )
                    if len(data) <= MAX_EVENT_BYTES:
                        break
            if len(data) > MAX_EVENT_BYTES or self.queued_bytes + len(data) > self.queue_bytes:
                self._drop("queue_byte_budget")
                return
            self.pending.put_nowait(data)
            self.queued_bytes += len(data)
        except queue.Full:
            self._drop("queue_full")
        except Exception:
            self._drop("serialization_failed")
        finally:
            self.guard.release()

    def _write(self, data):
        self.root.mkdir(parents=True, exist_ok=True)
        with budget_lock(self.root):
            if self.path is not None and self.path.stat().st_size + len(data) > self.file_bytes:
                self.path.rename(self.path.with_suffix(".closed.jsonl"))
                self.path = None
            files = list(self.root.glob("diag-*.jsonl"))
            total = sum(p.stat().st_size for p in files)
            for old in sorted(
                (
                    p
                    for p in files
                    if p.name.endswith(".closed.jsonl") and not p.with_suffix(".keep").exists()
                ),
                key=lambda p: p.stat().st_mtime_ns,
            ):
                if total + len(data) <= self.global_bytes:
                    break
                total -= old.stat().st_size
                old.unlink()
            if total + len(data) > self.global_bytes:
                raise OSError(errno.ENOSPC, "diagnostic budget occupied by active/protected segments")
            if self.path is None:
                self.segment += 1
                self.path = self.root / f"diag-{self.instance}-{self.segment}.jsonl"
            with self.path.open("ab") as output:
                output.write(data)

    def _drain(self):
        while not self.stopping.is_set() or not self.pending.empty():
            try:
                data = self.pending.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self._write(data)
            except OSError as error:
                self._drop(errno.errorcode.get(error.errno, "disk_error"))
            except Exception:
                self._drop("writer_failed")
            finally:
                with self.guard:
                    self.queued_bytes -= len(data)
                self.pending.task_done()

    def close(self):
        """Drena con plazo acotado; nunca espera indefinidamente al disco."""
        self.stopping.set()
        if threading.current_thread() is not self.writer:
            self.writer.join(timeout=2)
        if self.writer.is_alive():
            self._drop("shutdown_deadline")
        super().close()


def configure_diagnostics(root: Path | None = None, **limits) -> DiagnosticHandler:
    """Configuración de bootstrap; jamás crea handlers por ejecución."""
    global _sink
    if _sink is not None:
        _logger.removeHandler(_sink)
        _sink.close()
    _sink = DiagnosticHandler(root or diagnostic_root(), **limits)
    _logger.addHandler(_sink)
    return _sink


def ensure_diagnostics() -> DiagnosticHandler:
    """Bootstrap único del proceso, compartido por HTTP, worker y supervisor."""
    if _sink is None or _sink.stopping.is_set():
        return configure_diagnostics()
    return _sink


def native_capabilities() -> dict:
    """Distingue código disponible de validación nativa realizada en un incidente."""
    return {
        "jsonl": {
            "implemented": True,
            "available": True,
            "enabled": _sink is not None,
            "validated": False,
            "reason": "Validation is scoped to test receipts, not inferred here",
        },
        "nativeCapture": {
            "implemented": os.name == "nt",
            "available": os.name == "nt",
            "enabled": False,
            "validated": False,
            "reason": "Requires an explicit attempt, signed ProcDump, license and synthetic validation"
            if os.name == "nt"
            else "No native capture backend; OS validation NOT_RUN",
        },
    }


def diagnostic_event(event: str, *, component: str, error=None, context=None, level="INFO", **fields):
    """Entrada única desde telemetría. Un fallo diagnóstico nunca sustituye al error original."""
    try:
        if _sink is None:
            return
        payload = clean(
            {**context_fields(context), **{k: v for k, v in fields.items() if k in _FIELDS and v is not None}}
        )
        if level == "DEBUG" and not payload.pop("debugEnabled", False):
            # DEBUG is enabled only by the explicit attempt scope; never a global env switch.
            from local_control_center.process_supervision.context import CURRENT_EXECUTION

            ctx = context or CURRENT_EXECUTION.get()
            if not ctx or ctx.diagnostics_expires_at <= time.time():
                return
        if error is not None:
            payload["exceptionChain"] = exception_chain(error)
        payload.update(event=clean(event)[:160], component=clean(component)[:80])
        payload.setdefault("causeStatus", "UNKNOWN")
        _logger.log(getattr(logging, level, logging.INFO), "", extra={"diagnostic": payload})
    except Exception:
        if _sink is not None:
            _sink._drop("event_failed")


def activate_attempt(
    root: Path,
    execution_id: str,
    *,
    ttl_seconds: int,
    native_collector: str | None = None,
    executable_sha256: str | None = None,
):
    """Activa diagnóstico, no concede aprobación ni inicia/reintenta el trabajo."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", execution_id) or not 1 <= ttl_seconds <= 900:
        raise ValueError("Invalid execution identity or diagnostic expiry (1..900 seconds)")
    if native_collector and (
        not Path(native_collector).is_file() or not re.fullmatch(r"[a-fA-F0-9]{64}", executable_sha256 or "")
    ):
        raise ValueError("Native capture requires a collector and exact target SHA256")
    root.mkdir(parents=True, exist_ok=True)
    value = {
        "schemaVersion": 1,
        "executionId": execution_id,
        "expiresAt": time.time() + ttl_seconds,
        "nativeCollector": native_collector,
        "executableSha256": executable_sha256,
        "rustLog": RUST_LOG_FILTER,
        "enabled": True,
    }
    with (root / f"attempt-{execution_id}.json").open("x", encoding="utf-8") as output:
        json.dump(value, output)
    return value


def attempt_options(root: Path, execution_id: str | None):
    """Lee sólo la activación vigente de una identidad exacta."""
    if not execution_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", execution_id):
        return {"enabled": False}
    try:
        value = json.loads((root / f"attempt-{execution_id}.json").read_text(encoding="utf-8"))
        if value.get("executionId") == execution_id and 0 < value["expiresAt"] - time.time() <= 900:
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {"enabled": False}


def incident_events(root: Path, execution_id: str):
    """Lectura acotada exclusivamente a JSONL diagnósticos, no artefactos referidos."""
    for path in sorted(root.glob("diag-*.jsonl")):
        if path.is_symlink():
            continue
        with path.open("rb") as source:
            while line := source.readline(MAX_EVENT_BYTES + 1):
                if len(line) > MAX_EVENT_BYTES:
                    break
                try:
                    event = json.loads(line)
                    if event.get("executionId") == execution_id:
                        yield clean(event)
                except (ValueError, UnicodeError):
                    continue


def export_incident(root: Path, execution_id: str, output: Path):
    """Paquete de eventos saneados; nunca sigue evidenceRefs ni copia dumps o salidas."""
    digest, count = hashlib.sha256(), 0
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open("incident.jsonl", "w") as target:
            for event in incident_events(root, execution_id):
                event.pop("evidenceRefs", None)
                data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
                target.write(data)
                digest.update(data)
                count += 1
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "executionId": execution_id,
                    "events": count,
                    "sha256": digest.hexdigest(),
                    "nativeIncluded": False,
                    "artifactContentsIncluded": False,
                }
            ),
        )
    return {"events": count, "sha256": digest.hexdigest(), "nativeIncluded": False}


atexit.register(lambda: _sink.close() if _sink is not None else None)
