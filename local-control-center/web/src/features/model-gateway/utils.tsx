/**
 * Helpers de formato y celdas compartidas por los paneles del Model Gateway.
 * Normalizan valores potencialmente nulos del API a texto legible (con fallbacks), formatean montos
 * en USD y redactan secretos antes de pintarlos, evitando que cada panel reimplemente esa lógica.
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

/** Coacciona cualquier valor a string recortado; devuelve `fallback` cuando queda vacío o es nullish. */
export function text(value: unknown, fallback = 'n/a') {
	const result = String(value ?? '').trim();
	return result || fallback;
}

export function boolLabel(value: unknown) {
	return value ? 'yes' : 'no';
}

/** Formatea un monto USD con 4 decimales; devuelve `unknownLabel` si es nullish, vacío o no numérico. */
export function money(value: unknown, unknownLabel = 'unknown') {
	if (value === null || value === undefined || value === '') {
		return unknownLabel;
	}
	const number = Number(value);
	return Number.isFinite(number) ? `$${number.toFixed(4)}` : unknownLabel;
}

/** Une un arreglo en una lista separada por comas; cae a `text()` para valores no-arreglo. */
export function listLabel(value: unknown, noneLabel = 'none') {
	return Array.isArray(value)
		? value.map((item) => String(item)).join(', ') || noneLabel
		: text(value, noneLabel);
}

/** Pinta un valor en monoespaciado, sustituyéndolo por `[redacted]` si parece llave API o token Bearer. */
export function SecretSafeValue({ value }: { value: unknown }) {
	const { t } = useI18n();
	const rendered = text(value, t('app.modelGateway.runtime.notConfigured', 'not configured'));
	const unsafe = /sk-[A-Za-z0-9_-]+|Bearer\s+/i.test(rendered);
	return <span className="mono">{unsafe ? '[redacted]' : rendered}</span>;
}

/** Tarjeta KPI: muestra un valor destacado (con fallback `0`) sobre su etiqueta, en una `Surface` plana. */
export function Metric({ label, value }: { label: string; value: unknown }) {
	return (
		<Surface flat>
			<div className="metric-value">{text(value, '0')}</div>
			<div className="metric-label">{label}</div>
		</Surface>
	);
}
