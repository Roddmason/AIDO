"""Slice de integraciones de runtime: configuración persistida de runtimes/CLI con env solo como override.

Reúne el repositorio SQLite que persiste instalaciones de runtime, cuentas de CLI (sin tokens) y
preferencias (qué runtime es el predeterminado y sus perfiles por defecto), más la resolución que
aplica las variables de entorno únicamente como override sobre la configuración persistida. La
config normal vive en la base; el CLI queda como runtime predeterminado. No exporta símbolos: cada
consumidor importa de los submódulos `repository` o `config`.
"""

__all__: list[str] = []
