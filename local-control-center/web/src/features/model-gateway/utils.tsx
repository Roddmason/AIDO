/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';

export const EXECUTABLE_AGENT_ROLES = [
	'analyst',
	'product_owner',
	'technical_lead',
	'technical_lead_shadow',
	'developer',
	'backend_engineer',
	'frontend_engineer',
	'implementer',
	'qa',
	'qa_reviewer',
	'security_reviewer',
	'release_manager',
];

export function text(value: unknown, fallback = 'n/a') {
	const result = String(value ?? '').trim();
	return result || fallback;
}

export function boolLabel(value: unknown) {
	return value ? 'yes' : 'no';
}

export function money(value: unknown, unknownLabel = 'unknown') {
	if (value === null || value === undefined || value === '') {
		return unknownLabel;
	}
	const number = Number(value);
	return Number.isFinite(number) ? `$${number.toFixed(4)}` : unknownLabel;
}

export function listLabel(value: unknown, noneLabel = 'none') {
	return Array.isArray(value)
		? value.map((item) => String(item)).join(', ') || noneLabel
		: text(value, noneLabel);
}

export function SecretSafeValue({ value }: { value: unknown }) {
	const { t } = useI18n();
	const rendered = text(value, t('app.modelGateway.runtime.notConfigured', 'not configured'));
	const unsafe = /sk-[A-Za-z0-9_-]+|Bearer\s+/i.test(rendered);
	return <span className="mono">{unsafe ? '[redacted]' : rendered}</span>;
}

export function Metric({ label, value }: { label: string; value: unknown }) {
	return (
		<Surface flat>
			<div className="metric-value">{text(value, '0')}</div>
			<div className="metric-label">{label}</div>
		</Surface>
	);
}
