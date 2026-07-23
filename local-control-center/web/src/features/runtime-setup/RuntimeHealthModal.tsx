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

export function RuntimeHealthModal({
	open,
	onClose,
	alerts,
	onOpenSettings,
}: {
	open: boolean;
	onClose: () => void;
	alerts: RuntimeHealthAlert[];
	onOpenSettings: (section: string, providerId: string) => void;
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
						const copy = BLOCKER_COPY[alert.blockerType as BlockerCopyKey];
						const causeText =
							alert.reason ||
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
								{alert.lastError ? (
									<details className="thread-remediation-detail">
										<summary>{t('app.runtime.health.technical', 'Technical detail')}</summary>
										<pre className="mono">{alert.lastError}</pre>
									</details>
								) : null}
								<div className="thread-remediation-actions">
									<Button
										className="thread-remediation-primary"
										onClick={() => onOpenSettings(alert.settingsSection, alert.providerId)}
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
