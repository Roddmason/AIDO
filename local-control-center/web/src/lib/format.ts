/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
export function countByStatus(items: Array<{ status?: string }>, status: string) {
	return items.filter((item) => item.status === status).length;
}

export function shortId(id?: string | null) {
	if (!id) return 'none';
	return id.length > 18 ? `${id.slice(0, 12)}...` : id;
}

/**
 * Render free-text provider fields (reason, lastError, detected command, …) with
 * secret-bearing substrings masked. The backend already redacts these, but UI code
 * applies the same masking defensively so a leaked token can never reach the DOM.
 */
export function redactVisibleSecret(value: unknown, fallback = 'n/a'): string {
	const base = String(value ?? '').trim() || fallback;
	return base
		.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, '[redacted_secret]')
		.replace(/\bsk-[A-Za-z0-9_-]{8,}/gi, '[redacted_secret]')
		.replace(/([?&](?:api[_-]?key|token|secret)=)[^&\s]+/gi, '$1[redacted_secret]')
		.replace(/\b(?:api[_-]?key|token|secret)\s*[:=]\s*[^,\s;]+/gi, '[redacted_secret]');
}

export function toneForStatus(status?: string): 'ok' | 'warn' | 'danger' | 'info' {
	if (!status) return 'info';
	if (['completed', 'passed', 'approved', 'approved_for_integration', 'pr_created', 'active', 'available', 'security_passed'].includes(status)) return 'ok';
	if ([
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
	].includes(status)) return 'warn';
	if ([
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
	].includes(status)) return 'danger';
	return 'info';
}
