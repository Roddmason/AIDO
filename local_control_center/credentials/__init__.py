"""Slice de credenciales: gestión segura de secretos cuyo valor nunca se persiste ni se devuelve.

Reúne el repositorio SQLite de metadatos (referencias al backend, fingerprints y auditoría), los
adaptadores de backend (keyring por defecto en Windows Credential Manager, openbao, vault, dpapi_sqlite
opcional y env solo para bootstrap/CI) y el ``CredentialManager`` que orquesta crear, validar, rotar,
borrar y listar metadatos. El secreto vive en su backend; la base solo guarda dónde está, un hash con
sal para validar, y la bitácora. No exporta símbolos: cada consumidor importa de los submódulos.

@author Rodrigo Mason
"""

__all__: list[str] = []
