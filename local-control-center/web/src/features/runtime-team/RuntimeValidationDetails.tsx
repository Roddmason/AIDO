/**
 * Readable cause of a runtime test for the thread's AI team panel. A failed test lists every model
 * it tried with the HTTP status the provider answered and an excerpt of its body (full text behind a
 * disclosure, secrets masked), so a reachable gateway that rejects upstreams without an account no
 * longer reads as a bare `runtime_validation_failed`. Also explains why a row cannot be selected.
 * @author Rodrigo Mason
 */
import type { RuntimeTeamCandidate, RuntimeValidationResponse } from '../../api/client';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import { describeValidationReason } from '../runtime-setup/reasonCopy';

export type RuntimeValidationResult = RuntimeValidationResponse['validation'];
type Attempt = NonNullable<RuntimeValidationResult['attempts']>[number];
type Translate = (key: string, fallback?: string) => string;

/** Characters of provider text shown inline; the rest stays behind the disclosure. */
export const EVIDENCE_EXCERPT_CHARS = 160;

/** One-line excerpt of `text`, cut at `limit` characters with an ellipsis. */
export function evidenceExcerpt(text: string, limit = EVIDENCE_EXCERPT_CHARS): string {
	const single = text.replace(/\s+/g, ' ').trim();
	return single.length > limit ? `${single.slice(0, limit - 1)}…` : single;
}

function httpText(t: Translate, status: number | null | undefined): string {
	return typeof status === 'number'
		? `HTTP ${status}`
		: t('app.runtimeTeam.noHttpAnswer', 'no HTTP answer');
}

function Evidence({ text }: { text: string | null | undefined }) {
	const full = redactVisibleSecret(text, '');
	if (!full) return null;
	const short = evidenceExcerpt(full);
	if (short === full) {
		return <span className="runtime-team-evidence">{full}</span>;
	}
	return (
		<details className="runtime-team-evidence">
			<summary title={full}>{short}</summary>
			<p className="runtime-team-evidence-full">{full}</p>
		</details>
	);
}

function AttemptRow({ attempt }: { attempt: Attempt }) {
	const { t } = useI18n();
	const passed = attempt.status === 'validated';
	const status = passed
		? t('app.runtimeTeam.attemptPassed', 'passed')
		: `${t('app.runtimeTeam.attemptFailed', 'failed')} · ${httpText(t, attempt.httpStatus)}`;
	return (
		<li className="runtime-team-attempt">
			<span className="runtime-team-attempt-model">{attempt.model ?? '—'}</span>
			<span className="runtime-team-attempt-status">{status}</span>
			{!passed && attempt.reason ? (
				<span className="runtime-team-attempt-reason">
					{describeValidationReason(attempt.reason, t)}
				</span>
			) : null}
			{!passed ? <Evidence text={attempt.evidence} /> : null}
		</li>
	);
}

/** Cause of the last test this panel ran: the reason copy, then each model tried or the evidence. */
export function RuntimeValidationDetails({ result }: { result: RuntimeValidationResult }) {
	const { t } = useI18n();
	const attempts = result.attempts ?? [];
	// With several models tried, the headline counts them; each row carries its own cause.
	const reason =
		attempts.length > 0 && result.status === 'failed'
			? t('app.runtimeTeam.noModelPassed', 'None of the {count} models tried passed.').replace(
					'{count}',
					String(attempts.length),
				)
			: result.reason
				? describeValidationReason(result.reason, t)
				: null;
	return (
		<div className="runtime-team-reason field-help" data-testid="runtime-team-validation-details">
			{reason ? (
				<p className="runtime-team-reason-text">
					{result.model && attempts.length === 0 ? `${result.model} · ` : ''}
					{result.status === 'failed' && attempts.length === 0
						? `${httpText(t, result.httpStatus)} · `
						: ''}
					{reason}
				</p>
			) : null}
			{attempts.length > 0 ? (
				<ol
					className="runtime-team-attempts"
					aria-label={t('app.runtimeTeam.attempts', 'Models tested')}
				>
					{/* The backend never tries a model twice in one test, so the id is a stable key. */}
					{attempts.map((attempt) => (
						<AttemptRow key={attempt.model ?? 'no-model'} attempt={attempt} />
					))}
				</ol>
			) : (
				<Evidence text={result.evidence} />
			)}
		</div>
	);
}

/**
 * Why an enabled runtime cannot be ticked for this thread: never tested, expired, or its last test
 * failed (with the model and HTTP status of that failure). `null` when the row is selectable.
 */
export function notSelectableReason(t: Translate, candidate: RuntimeTeamCandidate): string | null {
	const validation = candidate.validation;
	if (candidate.eligibleRoles.length === 0 || validation.status === 'validated') return null;
	if (validation.status === 'policy_denied') {
		return t('app.runtimeTeam.whyPolicy', 'Blocked by this project policy.');
	}
	if (validation.status === 'failed') {
		const where = [
			validation.model,
			typeof validation.httpStatus === 'number' ? `HTTP ${validation.httpStatus}` : null,
		]
			.filter(Boolean)
			.join(', ');
		const base = t(
			'app.runtimeTeam.whyFailed',
			'Cannot join: its last test failed. Test it again to see the cause per model.',
		);
		return where ? `${base} (${where})` : base;
	}
	if (validation.status === 'stale') {
		const reason = validation.reason ? describeValidationReason(validation.reason, t) : '';
		return `${t('app.runtimeTeam.whyStale', 'Cannot join until it is tested again.')} ${reason}`.trim();
	}
	return t('app.runtimeTeam.whyNever', 'Cannot join until it passes a test: press Test.');
}
