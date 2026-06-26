/**
 * Helpers de presentación que mapean datos crudos a texto/semántica para la UI.
 * Cubre el truncado de IDs largos, el enmascarado defensivo de secretos en texto
 * libre, y la traducción de estados de dominio al tono visual de la paleta.
 */
import { maskSecrets } from './redaction';

/** Trunca IDs largos (>18) a `12 chars + …`; `'none'` si viene vacío o nulo. */
export function shortId(id?: string | null) {
	if (!id) return 'none';
	return id.length > 18 ? `${id.slice(0, 12)}...` : id;
}

/**
 * Render free-text provider fields (reason, lastError, detected command, …) with
 * secret-bearing substrings masked. The backend already redacts these, but UI code
 * applies the same masking defensively so a leaked token can never reach the DOM.
 * Shares its pattern set with `redactVisibleText` via `maskSecrets`; only the
 * marker differs (`[redacted_secret]`), which the Playwright contract pins.
 */
export function redactVisibleSecret(value: unknown, fallback = 'n/a'): string {
	const base = String(value ?? '').trim() || fallback;
	return maskSecrets(base, '[redacted_secret]');
}

/**
 * Renders a millisecond duration as a compact, human label (`820ms`, `4.2s`, `3m 5s`,
 * `1h 2m`). Returns an em dash for a null/negative input so an unknown duration reads as
 * absent rather than `0`.
 */
export function formatDurationMs(ms?: number | null): string {
	if (ms == null || ms < 0) return '—';
	if (ms < 1000) return `${Math.round(ms)}ms`;
	const totalSeconds = Math.round(ms / 1000);
	if (totalSeconds < 60) return `${(ms / 1000).toFixed(1)}s`;
	const minutes = Math.floor(totalSeconds / 60);
	const seconds = totalSeconds % 60;
	if (minutes < 60) return `${minutes}m ${seconds}s`;
	const hours = Math.floor(minutes / 60);
	return `${hours}h ${minutes % 60}m`;
}

/**
 * Renders a USD cost with adaptive precision (sub-cent costs keep four decimals so a
 * fraction-of-a-cent model call is not rounded to `$0.00`). Returns an em dash for a
 * null input so an unknown cost reads as absent rather than free.
 */
export function formatCostUsd(usd?: number | null): string {
	if (usd == null) return '—';
	const decimals = usd !== 0 && Math.abs(usd) < 0.01 ? 4 : 2;
	return `$${usd.toFixed(decimals)}`;
}

/**
 * Clasifica un estado de dominio (de cualquier etapa del pipeline) en el tono de la
 * paleta que lo representa. Estados desconocidos o ausentes caen a `'info'` (neutro).
 */
export function toneForStatus(status?: string): 'ok' | 'warn' | 'danger' | 'info' | 'pending' {
	if (!status) return 'info';
	if (
		[
			'completed',
			'passed',
			'approved',
			'approved_for_integration',
			'pr_created',
			'active',
			'available',
			'security_passed',
		].includes(status)
	)
		return 'ok';
	// Waiting on a human decision — use the distinct "pending" tone the palette defines.
	if (['pending', 'awaiting_permission', 'awaiting_approval'].includes(status)) return 'pending';
	if (
		[
			'queued',
			'running',
			'awaiting_human',
			'approval_required',
			'evidence_ready',
			'optional',
			'promoted_to_branch',
			'pr_unavailable',
			'skipped_with_reason',
			'needs_human_review',
			'warning',
		].includes(status)
	)
		return 'warn';
	if (
		[
			'failed',
			'blocked',
			'denied',
			'cancelled',
			'configuration_required',
			'unavailable',
			'runtime_unavailable',
			'qa_failed',
			'security_blocked',
			'devops_blocked',
			'promotion_failed',
			'pr_failed',
			'error',
			'critical',
		].includes(status)
	)
		return 'danger';
	return 'info';
}
