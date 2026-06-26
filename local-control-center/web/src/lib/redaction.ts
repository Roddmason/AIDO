/**
 * Última barrera del cliente contra fuga de secretos: enmascara tokens antes del DOM.
 * Invariante: todo texto libre de origen no confiable que se renderice debe pasar por
 * `maskSecrets` (vía un redactor de este módulo); ningún patrón de secreto soportado
 * (Bearer, sk-, ghp_/github_pat_, glpat-, xox*, AKIA…, pares clave=valor y query params)
 * puede alcanzar la UI sin reemplazarse. Defensa en profundidad: el backend ya redacta.
 */

/**
 * Shared secret-masking core: the single source of truth for which token shapes
 * are scrubbed before any value reaches the DOM. Both UI redactors call this so
 * their coverage is identical — a token caught by one can never slip the other.
 * The replacement `label` is a parameter only because the Playwright contract
 * pins two distinct markers — `[redacted]` (evidence) and `[redacted_secret]`
 * (runtime providers) — at different call sites; the patterns must stay shared.
 */
export function maskSecrets(input: string, label: string): string {
	return (
		input
			.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, label)
			.replace(/\bsk-[A-Za-z0-9_-]{8,}/gi, label)
			.replace(/\bghp_[A-Za-z0-9_]{12,}/gi, label)
			.replace(/\bgithub_pat_[A-Za-z0-9_]{20,}/gi, label)
			.replace(/\bglpat-[A-Za-z0-9_-]{12,}/gi, label)
			.replace(/\bxox[baprs]-[A-Za-z0-9-]{10,}/gi, label)
			.replace(/\bAKIA[0-9A-Z]{16}\b/g, label)
			// Credential-bearing connection strings (proto://user:password@host) and PEM private-key
			// blocks — mirrors the backend SECRET_VALUE_PATTERN so this layer is a true superset.
			.replace(/[a-z][a-z0-9+.-]{0,30}:\/\/[^\s:@/]{1,256}:[^\s@/]{1,256}@[^\s]{1,256}/gi, label)
			.replace(
				/-----BEGIN[A-Z0-9 ]{0,40}PRIVATE KEY-----[\s\S]{1,10000}?-----END[A-Z0-9 ]{0,40}PRIVATE KEY-----/gi,
				label,
			)
			.replace(
				/(["']?(?:api[_-]?key|authorization|credential|secret|token|password|client[_-]?secret|clientSecret|private[_-]?key|privateKey|OPENAI_API_KEY)["']?\s*[:=]\s*["']?)[^"',\s}]+(["']?)/gi,
				`$1${label}$2`,
			)
			.replace(/([?&](?:api[_-]?key|token|secret)=)[^&\s"]+/gi, `$1${label}`)
			.replace(
				/\b(?:api[_-]?key|authorization|credential|secret|token|password|client[_-]?secret|clientSecret|private[_-]?key|privateKey|OPENAI_API_KEY)\s*[:=]\s*"?[^",\s}]+/gi,
				label,
			)
	);
}

/**
 * Redactor para evidencia/datos arbitrarios: serializa objetos a JSON indentado y
 * enmascara con el marcador `[redacted]` que fija el contrato Playwright. Usa
 * `fallback` cuando el valor es `null`/`undefined`.
 */
export function redactVisibleText(value: unknown, fallback = 'not recorded') {
	const raw =
		typeof value === 'string'
			? value
			: value === undefined || value === null
				? fallback
				: JSON.stringify(value, null, 2);
	return maskSecrets(raw, '[redacted]');
}
