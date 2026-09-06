"""Captura incremental acotada con artefactos redactados y SHA-256.

@author Rodrigo Mason
"""

from __future__ import annotations

import codecs
import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import evidence_artifact_root
from local_control_center.shared.redaction import SECRET_VALUE_PATTERN, redact_secrets

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
REDACTION_LOOKBEHIND = 16_384


class ArtifactCapture:
    """Escribe salida saneada de manera incremental sin acumular el log completo."""

    def __init__(self, root: Path, stream: str, failure_event: Any, *, encoding: str = "utf-8"):
        if encoding not in {"utf-8", "utf-16-le"}:
            raise ValueError("Unsupported supervised output encoding")
        self.id = f"artifact-{uuid.uuid4()}"
        directory = evidence_artifact_root(root)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{self.id}.{stream}.log"
        self.file = self.path.open("xb")
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0
        self.total_bytes = 0
        self.pending = ""
        self.decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        self.failure_event = failure_event
        self.closed = False
        self.truncated = False

    def write(self, chunk: str | bytes) -> None:
        """Recibe bytes/texto y retiene un margen para secretos partidos entre lecturas."""
        if self.closed:
            raise ValueError("La captura ya está cerrada.")
        raw = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
        self.total_bytes += len(raw)
        if self.total_bytes > MAX_ARTIFACT_BYTES:
            self.truncated = True
            self.failure_event.set()
            return
        self.pending += self.decoder.decode(raw)
        cutoff = max(0, len(self.pending) - REDACTION_LOOKBEHIND)
        if "-----BEGIN" not in self.pending:
            line_boundary = self.pending.rfind("\n") + 1
            if not re.search(r"(?:Bearer|password\s*=)\s*$", self.pending[:line_boundary], re.I):
                cutoff = max(cutoff, line_boundary)
        else:
            # No escribir un PEM incompleto, aunque su tamaño exceda el patrón habitual.
            begin = self.pending.index("-----BEGIN")
            if "PRIVATE KEY-----" in self.pending[begin:] and "-----END" not in self.pending[begin:]:
                cutoff = min(cutoff, begin)
        for match in SECRET_VALUE_PATTERN.finditer(self.pending):
            if match.start() < cutoff < match.end():
                cutoff = match.start()
                break
        if cutoff:
            self._write_clean(self.pending[:cutoff])
            self.pending = self.pending[cutoff:]
        if len(self.pending) > 1024 * 1024:
            # Una credencial sin delimitador no puede crecer sin límite ni salir parcialmente.
            self.pending = "[redacted oversized sensitive output]"
            self.truncated = True
            self.failure_event.set()

    def _write_clean(self, text: str) -> None:
        data = str(redact_secrets(text)).encode("utf-8")
        self.file.write(data)
        self.file.flush()
        self.digest.update(data)
        self.size += len(data)

    def finish(self) -> dict[str, Any]:
        """Cierra la salida y devuelve metadatos verificables del artefacto."""
        if not self.closed:
            try:
                self._write_clean(self.pending + self.decoder.decode(b"", final=True))
                if self.truncated:
                    self._write_clean("\n[output capture truncated; execution terminated]\n")
            finally:
                self.file.close()
                self.closed = True
                self.pending = ""
        return {
            "artifactId": self.id,
            "path": str(self.path),
            "hash": self.digest.hexdigest(),
            "sizeBytes": self.size,
            "totalBytes": self.total_bytes,
            "truncated": self.truncated,
            "stream": self.stream,
        }


class CapturedReader:
    """Conserva el contrato de pipe y copia cada lectura a la evidencia incremental."""

    def __init__(self, source: Any, capture: ArtifactCapture):
        self.source = source
        self.capture = capture

    def read(self, size: int = -1):
        """Lee un fragmento y registra lo consumido."""
        result = self.source.read(size)
        if result:
            self.capture.write(result)
        return result

    def readline(self, size: int = -1):
        """Acota también una línea sin terminador emitida por un runtime."""
        result = self.source.readline(min(size, 65536) if size >= 0 else 65536)
        if result:
            self.capture.write(result)
        return result

    def read1(self, size: int = -1):
        """Drena bytes disponibles sin esperar llenar el buffer, conservando la redacción."""
        read = getattr(self.source, "read1", self.source.read)
        result = read(size)
        if result:
            self.capture.write(result)
        return result

    def __iter__(self):
        return self

    def __next__(self):
        value = self.readline()
        if not value:
            raise StopIteration
        return value

    def __getattr__(self, name: str):
        return getattr(self.source, name)
