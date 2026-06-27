# Estándar de documentación — AIDO / Local Control Center

> Reemplaza los banners genéricos (`AIDO backend source module` / `@file AIDO frontend source module`)
> por documentación **semántica**: cada archivo explica qué hace realmente. Lo aplica el equipo y lo
> verifica `tests_py/test_source_documentation_headers.py` (scanner de headers) + Ruff `D` (docstrings
> de API pública Python).

## 1. Principios

- **Semántica, no boilerplate.** El header dice qué provee el módulo y por qué existe, no "source module".
- **Sin redundancia.** No repetir el nombre del símbolo ni reescribir la firma. Documenta intención,
  contrato, efectos e invariantes — lo que el código no dice por sí solo.
- **Autoría por archivo.** Cada archivo productivo lleva `@author Rodrigo Mason` en su header de módulo
  (docstring Python / bloque `/** */` TS). Lo verifica el scanner.
- **Solo docstrings/JSDoc.** En código productivo no se conservan comentarios sueltos (`#`, `//`,
  `/* */` no-JSDoc): la documentación vive en el header y en las docstrings/JSDoc de API pública. Se
  preservan únicamente las directivas funcionales *load-bearing* (`noqa`, `type: ignore`, `pragma`,
  `coding`, shebang; `biome-ignore`, `@ts-*`, `eslint-*`, `<reference`).
- **No documentar lo obvio.** Variables triviales, getters de una línea evidentes, re-exports: sin ruido.
- **No tocar artefactos generados.** `local-control-center/web/src/api/generated/openapi.ts` queda excluido.

## 2. Header de módulo (obligatorio en todo archivo productivo)

Productivo = `local_control_center/**/*.py` (backend) y `local-control-center/web/src/**/*.{ts,tsx}`
(frontend, excl. `generated/`). `tests_py/`, `scripts/` y configs no requieren header semántico.

### Python (docstring de módulo, primer statement)

```python
"""Resuelve el ruteo de modelos por rol/políticas y registra cada decisión.

Aplica políticas de rol, presupuesto y cuota antes de elegir proveedor/modelo;
devuelve la decisión con su trazabilidad. No ejecuta el modelo (eso es execute_model_call).
"""
```

### TypeScript/TSX (bloque `/** */` al inicio)

```ts
/**
 * Página IDE de Model Gateway: orquesta los paneles de proveedores, ruteo y uso.
 * Compone los sub-paneles y centraliza el estado de selección; los datos llegan por props.
 */
```

**Prohibido** (lo rechaza el scanner): `AIDO backend source module`, `@file AIDO frontend source module`,
o un summary < 30 caracteres / vacío.

## 3. API pública

- **Python — toda función/clase/método público** (sin prefijo `_`) lleva docstring de intención
  (Ruff `D101/D102/D103`). Una línea basta si el propósito es simple; añade `Args/Returns` solo si
  aportan algo no evidente.
- **TS/TSX — exports públicos con JSDoc cuando el propósito no es trivial** (`/** ... */` sobre el
  export). Componentes de página, hooks, helpers de contrato, tipos no obvios. Un re-export o un tipo
  auto-explicativo no necesita JSDoc.

## 4. Reglas por dominio

- **Seguridad** (`local_control_center/security_policy/**`, `sandbox.py`, `agents/tool_broker.py`,
  `agents/credentials.py`): el header y/o las funciones públicas documentan **invariantes** que se
  garantizan y **qué se lanza** (`Raises:`) ante violación. Ej.: "Invariante: ninguna ejecución sin
  grant aprobado; lanza `PermissionError` si se intenta."
- **Repositorios** (`**/repository.py`): documentan los **límites de transacción** (qué se escribe
  atómicamente, qué `UPDATE/INSERT` agrupa, si confía en el `connection` del caller para el commit).

## 5. Qué verifica el scanner (`test_source_documentation_headers.py`)

1. Todo archivo productivo tiene header de módulo semántico (Python: docstring; TS: bloque `/** */`).
2. Rechaza los placeholders genéricos y los summaries vacíos/triviales (< 30 chars).
3. `security_policy/**` y `**repository.py` mencionan invariantes/`Raises`/transacciones según §4.
4. Excluye `generated/`. Exige `@author Rodrigo Mason` en cada header productivo; no exige copyright.

Las docstrings de API pública Python se enforcan vía Ruff (`D101/D102/D103` en `pyproject.toml`).
