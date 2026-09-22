/**
 * Client-facing "AI health" modal: lists every AI that currently needs attention and how to fix it.
 *
 * A runtime can lose its ability to run outside any thread (an expired CLI token, an exhausted quota,
 * a stopped local service), and the operator had no place to see that or act on it. This modal reads
 * the live provider poll ({@link deriveRuntimeAlerts}) and renders one plain-language remediation card
 * per blocked AI — cause, impact and a deep-link into the settings section that repairs it — reusing
 * the shared remediation copy so the wording matches a blocked loop. Purely presentational: it opens
 * Settings through the injected callback and never mutates state itself.
 * @author Rodrigo Mason
 */

import type { RuntimeHealthAlert } from '../../app/runtimeHealth';
import { Button } from '../../components/ui/Button';
import { Dialog } from '../../components/ui/Dialog';
import { useI18n } from '../../i18n/I18nProvider';
import { BLOCKER_COPY } from '../shell/remediationPresentation';

type BlockerCopyKey = keyof typeof BLOCKER_COPY;

/**
 * Runtime-only blocker: a CLI sign-in that was verified and whose check merely expired. It is not a
 * loop blocker, so it lives here instead of the remediation vocabulary, and its fix is revalidating.
 */
const VALIDATION_EXPIRED_BLOCKER = 'runtime_validation_expired';
const VALIDATION_EXPIRED_COPY = {
	titleKey: 'app.runtime.health.blocker.runtime_validation_expired.title',
	titleFallback: 'Runtime validation expired',
	explanationKey: 'app.runtime.health.blocker.runtime_validation_expired.explanation',
	explanationFallback:
		"This runtime's sign-in was already verified, but that check expired. Revalidate it; you do not need to sign in again.",
	impactKey: 'app.runtime.health.blocker.runtime_validation_expired.impact',
	impactFallback: 'Agent steps that use this runtime wait until it is revalidated.',
	settingsLabelKey: 'app.threads.remediation.action.validateRuntime',
	settingsLabelFallback: 'Revalidate runtime',
};

/**
 * Plain-language text for the machine reason codes the readiness projection joins into `reason`.
 * Host-capacity codes say the machine lacks room, so they never read as a configuration problem.
 */
const REASON_COPY = new Map<string, { key: string; fallback: string }>([
	[
		'health_check_required',
		{
			key: 'app.runtime.health.reason.health_check_required',
			fallback: 'The runtime has not passed a recent health check.',
		},
	],
	[
		'minimum_free_memory',
		{
			key: 'app.runtime.health.reason.minimum_free_memory',
			fallback:
				'This machine does not have enough free RAM right now; this is not a configuration problem.',
		},
	],
	[
		'hard_memory_floor',
		{
			key: 'app.runtime.health.reason.hard_memory_floor',
			fallback:
				"This machine's free RAM is below its safety floor; this is not a configuration problem.",
		},
	],
	[
		'aggregate_memory_budget',
		{
			key: 'app.runtime.health.reason.aggregate_memory_budget',
			fallback:
				"The AI work already running uses this machine's whole RAM budget; this is not a configuration problem.",
		},
	],
	[
		'minimum_free_disk',
		{
			key: 'app.runtime.health.reason.minimum_free_disk',
			fallback:
				'This machine does not have enough free disk space; this is not a configuration problem.',
		},
	],
]);

/** Renders a comma-joined list of reason codes as text, keeping unknown codes (and prose) verbatim. */
function describeReason(reason: string, t: (key: string, fallback?: string) => string): string {
	const codes = reason.split(', ');
	if (!codes.some((code) => REASON_COPY.has(code))) return reason;
	return codes
		.map((code) => {
			const copy = REASON_COPY.get(code);
			return copy ? t(copy.key, copy.fallback) : code;
		})
		.join(' ');
}

export function RuntimeHealthModal({
	open,
	onClose,
	alerts,
	onOpenSettings,
	onRevalidate,
}: {
	open: boolean;
	onClose: () => void;
	alerts: RuntimeHealthAlert[];
	onOpenSettings: (section: string, providerId: string) => void;
	/** Re-runs the runtime's health check; the primary action of an expired validation. */
	onRevalidate: (providerId: string) => void;
}) {
	const { t } = useI18n();

	return (
		<Dialog open={open} onClose={onClose} label={t('app.runtime.health.title', 'AI health')}>
			<div className="thread-remediation-list">
				{alerts.length === 0 ? (
					<p className="thread-remediation-explanation">
						{t('app.runtime.health.allReady', 'Every configured AI is ready to run.')}
					</p>
				) : (
					alerts.map((alert) => {
						const revalidates = alert.blockerType === VALIDATION_EXPIRED_BLOCKER;
						const copy = revalidates
							? VALIDATION_EXPIRED_COPY
							: BLOCKER_COPY[alert.blockerType as BlockerCopyKey];
						const causeText =
							describeReason(alert.reason, t) ||
							t('app.runtime.health.causeUnknown', 'The runtime reported no further detail.');
						return (
							<article key={alert.providerId} className="thread-remediation-card">
								<h3 className="thread-remediation-title">
									{alert.displayName}
									{copy ? ` — ${t(copy.titleKey, copy.titleFallback)}` : ''}
								</h3>
								{copy ? (
									<p className="thread-remediation-explanation">
										{t(copy.explanationKey, copy.explanationFallback)}
									</p>
								) : null}
								<dl className="thread-remediation-facts">
									<div>
										<dt>{t('app.runtime.health.cause', 'Cause')}</dt>
										<dd>{causeText}</dd>
									</div>
									{copy ? (
										<div>
											<dt>{t('app.runtime.health.impact', 'Impact')}</dt>
											<dd>{t(copy.impactKey, copy.impactFallback)}</dd>
										</div>
									) : null}
								</dl>
								{alert.loginCommand ? (
									<div className="thread-remediation-facts">
										<div>
											<dt>{t('app.runtime.health.loginCommand', 'Sign in from your terminal')}</dt>
											<dd>
												<code className="mono">{alert.loginCommand}</code>
											</dd>
										</div>
									</div>
								) : null}
								{alert.lastError ? (
									<details className="thread-remediation-detail">
										<summary>{t('app.runtime.health.technical', 'Technical detail')}</summary>
										<pre className="mono">{alert.lastError}</pre>
									</details>
								) : null}
								<div className="thread-remediation-actions">
									<Button
										className="thread-remediation-primary"
										onClick={() =>
											revalidates
												? onRevalidate(alert.providerId)
												: onOpenSettings(alert.settingsSection, alert.providerId)
										}
									>
										{t(
											copy?.settingsLabelKey ?? 'app.runtime.health.openSettings',
											copy?.settingsLabelFallback ?? 'Open settings to fix it',
										)}
									</Button>
								</div>
							</article>
						);
					})
				)}
			</div>
		</Dialog>
	);
}
