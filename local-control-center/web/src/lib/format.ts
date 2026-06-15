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
