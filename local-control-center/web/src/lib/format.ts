export function countByStatus(items: Array<{ status?: string }>, status: string) {
	return items.filter((item) => item.status === status).length;
}

export function shortId(id?: string | null) {
	if (!id) return 'none';
	return id.length > 18 ? `${id.slice(0, 12)}...` : id;
}

export function toneForStatus(status?: string): 'ok' | 'warn' | 'danger' | 'info' {
	if (!status) return 'info';
	if (['completed', 'passed', 'approved', 'approved_for_integration', 'pr_created', 'active', 'available'].includes(status)) return 'ok';
	if (['queued', 'running', 'awaiting_human', 'approval_required', 'optional', 'promoted_to_branch', 'pr_unavailable'].includes(status)) return 'warn';
	if (['failed', 'blocked', 'denied', 'cancelled', 'pr_failed'].includes(status)) return 'danger';
	return 'info';
}
