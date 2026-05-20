from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


McpTransport = Literal["stdio"]


class McpServerRegisterRequest(BaseModel):
    id: str
    command: str
    transport: McpTransport = "stdio"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("transport", mode="before")
    @classmethod
    def normalize_transport(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class McpServerResponse(BaseModel):
    mcp_server: dict[str, Any] = Field(alias="mcpServer")


class IdeConnectionUpsertRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    editor: str = "unknown"
    workspace_root: str | None = Field(default=None, alias="workspaceRoot")
    status: str = "connected"
    open_files: list[Any] = Field(default_factory=list, alias="openFiles")
    diagnostics: list[Any] = Field(default_factory=list)
    selection: dict[str, Any] = Field(default_factory=dict)
    terminal_context: dict[str, Any] = Field(default_factory=dict, alias="terminalContext")


class IdeConnectionResponse(BaseModel):
    ide_connection: dict[str, Any] = Field(alias="ideConnection")
