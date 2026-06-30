/**
 * Control-plane boot screen shown while the handshake + overview are still loading (or retrying).
 *
 * Honest loading UX: the backend's readiness ETA is unknown (the hook retries the handshake until
 * FastAPI is up), so this shows an INDETERMINATE animated bar — never a timer-driven fake fill — plus
 * an animated signal mark, a static legend of the components it brings online, and an
 * automatic-reconnect status when contact is lost. All motion is CSS and degrades under
 * prefers-reduced-motion. The status line is a polite live region so AT is told when contact drops.
 * @author Rodrigo Mason
 */
import { useI18n } from '../i18n/I18nProvider';

/** The components the control plane brings online; a static legend — no per-component progress is
 *  tracked (the hook flips one shared loading/connected flag), so the dots never imply per-item state. */
const BOOT_COMPONENTS: ReadonlyArray<{ key: string; fallback: string }> = [
	{ key: 'app.boot.componentApi', fallback: 'FastAPI v1 session' },
	{ key: 'app.boot.componentDb', fallback: 'SQLite control state' },
	{ key: 'app.boot.componentRuntimes', fallback: 'Runtime providers' },
];

/** Full-pane boot screen. `error` is non-empty while the control plane is unreachable (retrying). */
export function BootScreen({ error }: { error: string }) {
	const { t } = useI18n();
	const hasError = Boolean(error);
	const visualState = hasError ? 'error' : 'connecting';

	return (
		<div className="boot-screen">
			<div className="boot-card">
				<div className="boot-mark" aria-hidden="true">
					<span className="boot-mark-ring" />
					<span className="boot-mark-ring" />
					<span className="boot-mark-core" />
				</div>

				<h1 className="boot-title">{t('app.boot.loading', 'Loading control plane')}</h1>
				<p className="boot-subtitle">
					{hasError
						? t(
								'app.boot.reconnecting',
								'Lost contact with the control plane — reconnecting automatically…',
							)
						: t('app.boot.loadingBody', 'Bringing the local control plane online.')}
				</p>

				<div
					className="boot-progress"
					data-state={visualState}
					role="progressbar"
					aria-label={t('app.boot.progressLabel', 'Control plane startup')}
					aria-valuetext={
						hasError
							? t(
									'app.boot.reconnecting',
									'Lost contact with the control plane — reconnecting automatically…',
								)
							: t('app.boot.statusConnecting', 'Establishing connection…')
					}
				/>

				<ul
					className="boot-steps"
					aria-label={t('app.boot.componentsLabel', 'Components coming online')}
				>
					{BOOT_COMPONENTS.map((component) => (
						<li className="boot-step" key={component.key} data-state={visualState}>
							<span className="boot-step-dot" aria-hidden="true" />
							{t(component.key, component.fallback)}
						</li>
					))}
				</ul>

				<p className="boot-status mono" data-state={visualState} role="status">
					{hasError ? error : t('app.boot.statusConnecting', 'Establishing connection…')}
				</p>
			</div>
		</div>
	);
}
