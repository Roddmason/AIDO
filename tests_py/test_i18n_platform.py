from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "local_control_center" / "i18n" / "default_catalog.json"
WEB_SRC = ROOT / "local-control-center" / "web" / "src"


UI_COPY_PROPERTY_RE = re.compile(
    r"""
    (?:
        \b(?:title|kicker|summary|body|label|hint|emptyTitle|emptyBody|placeholder|ariaLabel|newProjectLabel)\s*[:=]\s*
        |setError\(\s*
    )
    (?P<quote>['"])(?P<value>(?:\\.|(?! (?P=quote) ).)*?)(?P=quote)
    """,
    re.VERBOSE,
)
JSX_TEXT_RE = re.compile(r">(?P<value>[A-Z][^<>{}\n]{2,120})<")
SPANISH_COPY_RE = re.compile(
    r"^\s*("
    r"Apareceran|Configuracion|Configuraciones|Crea\s|Crear\s|El\s|Fallo\s|Flujos\s|Gobierno\s|"
    r"Guardia\s|Historial\s|Importar\s|La\s+seleccion|Los\s+proyectos|Memoria\s|No\s+hay|"
    r"Politica\s|Proyecto\s|Proyectos\s|Registro\s|Rueda\s|Seleccion\s|Siguiente\s|Solo\s"
    r")\b",
    re.IGNORECASE,
)


def _is_static_ui_copy(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip())
    if not normalized or not re.search(r"[A-Za-z]", normalized):
        return False
    if normalized.startswith(("./", "/", "\\", "#", "$")):
        return False
    if re.fullmatch(r"[A-Z0-9_ -]{1,6}", normalized):
        return False
    if re.fullmatch(r"[a-z0-9_.:/-]+", normalized):
        return False
    if re.search(r"\.(json|md|toml|xml|txt|py|ps1|yaml|yml|lock)\b", normalized):
        return False
    if normalized in {"React", "TypeScript", "PowerShell", "SQLite", "FastAPI", "Ollama"}:
        return False
    return not SPANISH_COPY_RE.search(normalized)


def extract_static_ui_copy() -> set[str]:
    values: set[str] = set()
    for path in WEB_SRC.rglob("*.tsx"):
        source = path.read_text(encoding="utf-8")
        for match in UI_COPY_PROPERTY_RE.finditer(source):
            # Unescape only quote/backslash escapes (\' \" \\); leave \n, \t, \uXXXX intact
            # so extracted copy is not corrupted (e.g. "\\n" must stay "\\n", not become "n").
            value = re.sub(r"\\(['\"\\])", r"\1", match.group("value"))
            if _is_static_ui_copy(value):
                values.add(re.sub(r"\s+", " ", value.strip()))
        for match in JSX_TEXT_RE.finditer(source):
            value = re.sub(r"\s+", " ", match.group("value").strip())
            if _is_static_ui_copy(value):
                values.add(value)
    return values


def test_default_i18n_catalog_is_bilingual_and_complete() -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))

    assert payload["defaultLanguage"] == "en"
    assert {language["code"] for language in payload["languages"]} >= {"en", "es"}
    assert payload["translations"]

    missing: list[str] = []
    identical: list[str] = []
    for key, translations in payload["translations"].items():
        for language in ("en", "es"):
            if not str(translations.get(language, "")).strip():
                missing.append(f"{key}.{language}")
        if translations.get("en") == translations.get("es"):
            identical.append(key)

    assert missing == []
    assert identical == []


def test_static_frontend_copy_is_registered_in_default_i18n_catalog() -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    catalog_values = {
        str(value).strip() for values in payload["translations"].values() for value in values.values()
    }
    static_ui_copy = extract_static_ui_copy()
    missing = sorted(static_ui_copy - catalog_values)

    assert missing == []


def test_i18n_catalog_can_be_edited_without_redeploy(tmp_path: Path) -> None:
    sys.modules["faiss"] = None

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    client = TestClient(app)
    token = client.get("/api/v1/security/handshake").json()["token"]

    catalog = client.get("/api/v1/i18n/catalog").json()
    assert catalog["translations"]["app.brand.kicker"]["en"] == "Windows native · v1 only"
    assert catalog["translations"]["app.brand.kicker"]["es"] == "Nativo Windows · solo v1"

    denied = client.put(
        "/api/v1/i18n/catalog",
        json={
            "defaultLanguage": "en",
            "languages": catalog["languages"],
            "translations": {
                **catalog["translations"],
                "app.brand.kicker": {
                    **catalog["translations"]["app.brand.kicker"],
                    "es": "Windows local · v1",
                },
            },
        },
    )
    assert denied.status_code == 403

    translations_with_french = {
        key: {**values, "fr": values["en"]} for key, values in catalog["translations"].items()
    }
    translations_with_french["app.brand.kicker"]["es"] = "Windows local · v1"
    translations_with_french["app.brand.kicker"]["fr"] = "Windows local · v1"

    updated = client.put(
        "/api/v1/i18n/catalog",
        headers={"X-Local-Control-Token": token},
        json={
            "defaultLanguage": "en",
            "languages": [
                *catalog["languages"],
                {"code": "fr", "name": "French", "nativeName": "Français", "enabled": True},
            ],
            "translations": translations_with_french,
        },
    )
    assert updated.status_code == 200

    persisted = client.get("/api/v1/i18n/catalog").json()
    assert {language["code"] for language in persisted["languages"]} >= {"en", "es", "fr"}
    assert persisted["translations"]["app.brand.kicker"]["es"] == "Windows local · v1"
    assert persisted["translations"]["app.brand.kicker"]["fr"] == "Windows local · v1"

    runtime.close()


def test_existing_i18n_catalog_merges_missing_default_keys_without_overwriting_edits(tmp_path: Path) -> None:
    sys.modules["faiss"] = None

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    client = TestClient(app)
    token = client.get("/api/v1/security/handshake").json()["token"]

    partial = client.put(
        "/api/v1/i18n/catalog",
        headers={"X-Local-Control-Token": token},
        json={
            "defaultLanguage": "en",
            "languages": [
                {"code": "en", "name": "English", "nativeName": "English", "enabled": True},
                {"code": "es", "name": "Spanish", "nativeName": "Espanol", "enabled": True},
            ],
            "translations": {
                "app.brand.kicker": {
                    "en": "Windows native · v1 only",
                    "es": "Windows local editado",
                },
            },
        },
    )
    assert partial.status_code == 200

    merged = client.get("/api/v1/i18n/catalog").json()
    assert merged["translations"]["app.brand.kicker"]["es"] == "Windows local editado"
    assert merged["translations"]["app.workbench.team.title"]["en"] == "AI delivery team"
    assert merged["translations"]["app.workbench.team.title"]["es"] == "Equipo IA de entrega"

    runtime.close()


T_CALL_LITERAL_RE = re.compile(r"\bt\(\s*(['\"])(?P<key>[A-Za-z0-9_.-]+)\1\s*,\s*(['\"])(?:\.|(?!\3).)*\3")


def test_every_static_t_call_key_is_registered_in_default_catalog() -> None:
    """Toda clave literal usada en t('clave','fallback') debe existir en el catálogo bilingüe.

    El gate de copy estático no captura fallbacks de t() (solo props y texto JSX), así que 211
    claves llegaron a producción sin entrada: la UI en español renderizaba el fallback inglés.
    Las claves construidas dinámicamente (template literals) quedan fuera por diseño.
    """
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    registered = set(payload["translations"].keys())

    used: set[str] = set()
    for path in sorted(WEB_SRC.rglob("*.ts*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        for match in T_CALL_LITERAL_RE.finditer(path.read_text(encoding="utf-8")):
            used.add(match.group("key"))

    missing = sorted(used - registered)
    assert missing == [], f"claves t() sin entrada en default_catalog.json: {missing}"
