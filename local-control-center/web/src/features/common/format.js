export function asArray(value) {
	return Array.isArray(value) ? value : [];
}

export function formatDate(value) {
	if (!value) return 'not recorded';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return String(value);
	return new Intl.DateTimeFormat(undefined, {
		month: 'short',
		day: '2-digit',
		hour: '2-digit',
		minute: '2-digit',
	}).format(date);
}

export function countBy(items, key) {
	return asArray(items).reduce((counts, item) => {
		const value = item?.[key] || 'unknown';
		counts[value] = (counts[value] || 0) + 1;
		return counts;
	}, {});
}

export function shortId(value) {
	if (!value) return 'none';
	const text = String(value);
	return text.length > 18 ? `${text.slice(0, 12)}...${text.slice(-4)}` : text;
}

export function toneForStatus(status) {
	if (['completed', 'approved', 'ok', 'indexed'].includes(status)) return 'moss';
	if (['running', 'queued'].includes(status)) return 'amber';
	if (['failed', 'approval_required', 'pending', 'denied', 'degraded'].includes(status)) return 'oxblood';
	return undefined;
}
