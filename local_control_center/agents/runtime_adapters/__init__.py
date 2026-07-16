"""Concrete runtime adapters that execute approved work and capture evidence.

Package facade for the execution-request/result contracts and the adapters that turn a
broker-approved tool call into a real side effect: workspace-bound subprocesses, CLI version
checks, Ollama/OpenAI-compatible/Anthropic chat calls, provider-factory execution, and
guarded workspace file patches. Every adapter enforces structured argv, workspace
containment, and bounded timeouts; output is redacted, persisted as artifacts, and packaged
as evidence. Re-exports the full public API so `local_control_center.agents.runtime_adapters`
imports keep working unchanged.

@author Rodrigo Mason
"""

from .anthropic import (
    ANTHROPIC_VERSION,
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_ANTHROPIC_MAX_TOKENS,
    AnthropicAdapter,
)
from .common import RUNTIME_ADAPTER_TOOLS
from .models import (
    RuntimeAdapter,
    RuntimeExecutionAdapter,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
)
from .ollama import OllamaAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .provider_factory import ProviderFactoryAdapter
from .registry import (
    RuntimeAdapterBrokerAdapter,
    RuntimeAdapterRegistry,
    UnavailableRuntimeAdapter,
)
from .subprocess_adapter import (
    CLI_VERSION_ADAPTERS,
    VERSION_ARGS,
    CliVersionAdapter,
    RestrictedSubprocessAdapter,
)
from .workspace_patch import (
    PATCH_BYTES_LIMIT,
    PATCH_FILE_LIMIT,
    SENSITIVE_PATH_PARTS,
    WorkspacePatchBrokerAdapter,
)

__all__ = [
    "ANTHROPIC_VERSION",
    "CLI_VERSION_ADAPTERS",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "DEFAULT_ANTHROPIC_MAX_TOKENS",
    "PATCH_BYTES_LIMIT",
    "PATCH_FILE_LIMIT",
    "RUNTIME_ADAPTER_TOOLS",
    "SENSITIVE_PATH_PARTS",
    "VERSION_ARGS",
    "AnthropicAdapter",
    "CliVersionAdapter",
    "OllamaAdapter",
    "OpenAICompatibleAdapter",
    "ProviderFactoryAdapter",
    "RestrictedSubprocessAdapter",
    "RuntimeAdapter",
    "RuntimeAdapterBrokerAdapter",
    "RuntimeAdapterRegistry",
    "RuntimeExecutionAdapter",
    "RuntimeExecutionRequest",
    "RuntimeExecutionResult",
    "UnavailableRuntimeAdapter",
    "WorkspacePatchBrokerAdapter",
]
