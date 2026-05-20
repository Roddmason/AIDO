from __future__ import annotations

from .base import CliRuntime, RuntimeRequest, RuntimeResult


class ManualRuntime(CliRuntime):
    runtime_id = "manual"
    display_name = "Manual Runtime"

    def __init__(self, *, executable: str = "manual", mock: bool = True):
        super().__init__(executable=executable, mock=mock)

    def build_command(self, request: RuntimeRequest) -> list[str]:
        self._validate_workspace(request)
        self._validate_safe_args(request)
        return ["manual", request.prompt]

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        return RuntimeResult(runtime=self.runtime_id, status="created", command=self.build_command(request), stdout="Manual operator session created")
