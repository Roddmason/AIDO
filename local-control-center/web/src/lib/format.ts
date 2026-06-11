export function countByStatus(items: Array<{ status?: string }>, status: string) {
	return items.filter((item) => item.status === status).length;
}

export function shortId(id?: string | null) {
	if (!id) return 'none';
	return id.length > 18 ? `${id.slice(0, 12)}...` : id;
}

export function toneForStatus(status?: string): 'ok' | 'warn' | 'danger' | 'info' {
	if (!status) return 'info';
	if (['completed', 'passed', 'approved', 'approved_for_integration', 'active', 'available'].includes(status)) return 'ok';
	if (['queued', 'running', 'awaiting_human', 'approval_required', 'optional'].includes(status)) return 'warn';
	if (['failed', 'blocked', 'denied', 'cancelled'].includes(status)) return 'danger';
	return 'info';
}
