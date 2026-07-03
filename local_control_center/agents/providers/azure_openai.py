"""Integra Azure OpenAI reutilizando el transporte estilo OpenAI con auth `api-key`.

Azure habla el dialecto OpenAI en su ruta GA `v1` (`{resource}.openai.azure.com/openai/v1`),
pero se autentica con la cabecera `api-key` en vez de `Authorization: Bearer` y usa un endpoint
por recurso que no admite un valor por defecto. Este adaptador solo sobreescribe el esquema de
auth y la resolución de la base URL (siempre provista por el operador); el resto del transporte
—`/models`, `/chat/completions`, parseo de uso— se hereda del proveedor OpenAI-compatible.
El nombre del modelo es el del *deployment* de Azure y viaja en el cuerpo, como en OpenAI.

@author Rodrigo Mason
"""

from __future__ import annotations

from local_control_center.agents.credentials import CredentialResolver

from .openai_compatible import OpenAICompatibleProvider


class AzureOpenAIProvider(OpenAICompatibleProvider):
    """Proveedor Azure OpenAI: dialecto OpenAI con cabecera `api-key` y base URL por recurso."""

    def __init__(
        self,
        *,
        provider_id: str = "azure_openai",
        base_url: str | None = None,
        credential_ref: str | None = None,
    ):
        # La base URL de Azure es específica del recurso; no hay default ni fallback por entorno
        # (a diferencia del proveedor OpenAI-compatible genérico), así que se resuelve solo del arg.
        self.provider_id = provider_id
        self.base_url = (base_url or "").rstrip("/")
        self.credential_ref = credential_ref or ""
        self.credential_resolver = CredentialResolver()

    def _auth_headers(self) -> dict[str, str]:
        """Azure autentica la API key con la cabecera `api-key`, no con `Authorization: Bearer`."""
        return {"api-key": self._credential()}
