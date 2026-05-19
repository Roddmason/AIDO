# Ejecución local recomendada

Para desarrollo diario y operación del control plane, la opción técnicamente superior es levantarlo directamente en el host:

- usa tus credenciales reales de `claude`, `codex` y `gemini` sin mounts intermedios;
- ve repositorios y ramas locales sin mapping artificial de paths;
- evita falsos negativos por shells o credenciales encapsuladas fuera del proceso local;
- reduce fricción en `git`, `workspace` y plugins.

## Comandos

1. Construye el dashboard local:

```powershell
corepack pnpm@10.24.0 run build:control-center
```

2. Levanta el control center local:

```powershell
corepack pnpm@10.24.0 start
```

## URL por defecto

```text
http://127.0.0.1:4310
```

## Criterio operativo

- `pnpm start` es la entrada canónica.
- No se recomienda levantar variantes paralelas con otros puertos desde este repo.
- El backend operativo es FastAPI en Windows nativo mediante `uv run python -m local_control_center`.
- Si necesitas el TUI Node legacy para debugging puntual, ejecútalo directamente con:

```powershell
node local-control-center/dist/main.mjs --no-dashboard
```
