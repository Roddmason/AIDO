export function redactVisibleText(value: unknown, fallback = 'not recorded') {
	const raw = typeof value === 'string'
		? value
		: value === undefined || value === null
			? fallback
			: JSON.stringify(value, null, 2);
	return raw
		.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, '[redacted]')
		.replace(/\bsk-[A-Za-z0-9_-]{8,}/gi, '[redacted]')
		.replace(/\bghp_[A-Za-z0-9_]{12,}/gi, '[redacted]')
		.replace(/\bgithub_pat_[A-Za-z0-9_]{20,}/gi, '[redacted]')
		.replace(/\bglpat-[A-Za-z0-9_-]{12,}/gi, '[redacted]')
		.replace(/\bxox[baprs]-[A-Za-z0-9-]{10,}/gi, '[redacted]')
		.replace(/\bAKIA[0-9A-Z]{16}\b/g, '[redacted]')
		.replace(/(["']?(?:api[_-]?key|authorization|credential|secret|token|password|client[_-]?secret|clientSecret|private[_-]?key|privateKey|OPENAI_API_KEY)["']?\s*[:=]\s*["']?)[^"',\s}]+(["']?)/gi, '$1[redacted]$2')
		.replace(/([?&](?:api[_-]?key|token|secret)=)[^&\s"]+/gi, '$1[redacted]')
		.replace(/\b(?:api[_-]?key|authorization|credential|secret|token|password|client[_-]?secret|clientSecret|private[_-]?key|privateKey|OPENAI_API_KEY)\s*[:=]\s*"?[^",\s}]+/gi, '[redacted]');
}
