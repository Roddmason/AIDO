/**
 * Descriptor de error async desacoplado de la traducción: captura en el efecto, traduce en render.
 * Evita el stale-closure de locale cuando el idioma cambia con una petición en vuelo.
 * @author Rodrigo Mason
 */

/**
 * Descriptor de error de una operacion async, desacoplado de la traduccion.
 * `raw` = mensaje real de un Error (no se traduce); `fallbackKey`/`fallback` = mensaje
 * generico a traducir EN RENDER con el `t` vigente, evitando el stale-closure de locale
 * si el idioma cambia con una peticion en vuelo.
 */
export type AsyncError = { raw: string } | { fallbackKey: string; fallback: string };

/** Construye el descriptor en el efecto SIN llamar a `t` (la traduccion ocurre en render). */
export function toAsyncError(error: unknown, fallbackKey: string, fallback: string): AsyncError {
	return error instanceof Error ? { raw: error.message } : { fallbackKey, fallback };
}

/** Resuelve el descriptor a texto en render con el `t` actual. `null` -> cadena vacia. */
export function resolveAsyncError(
	error: AsyncError | null,
	t: (key: string, fallback?: string) => string,
): string {
	if (!error) {
		return '';
	}
	return 'raw' in error ? error.raw : t(error.fallbackKey, error.fallback);
}
