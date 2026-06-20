/**
 * Collapsible bottom dock for logs / terminal output.
 *
 * Presentational surface only: it renders the titled, closable frame that the
 * shell mounts inside a resizable bottom pane. Live log/terminal content is wired
 * in by the caller; until then it shows an empty state.
 */
import { Terminal, X } from 'lucide-react';

import { EmptyState } from '../components/primitives';
import { useI18n } from '../i18n/I18nProvider';

/** Titled, closable bottom dock surface; `onClose` collapses the pane. */
export function BottomPanel({ onClose }: { onClose: () => void }) {
	const { t } = useI18n();
	const title = t('app.bottomPanel.title', 'Bottom panel');
	return (
		<section className="bottom-panel" aria-label={title}>
			<header className="bottom-panel-header">
				<span className="bottom-panel-title">
					<Terminal aria-hidden="true" size={15} />
					{title}
				</span>
				<button
					className="icon-button"
					type="button"
					aria-label={t('app.bottomPanel.close', 'Close bottom panel')}
					onClick={onClose}
				>
					<X aria-hidden="true" size={16} />
				</button>
			</header>
			<div className="bottom-panel-body">
				<EmptyState
					title={t('app.bottomPanel.emptyTitle', 'No active session')}
					body={t('app.bottomPanel.emptyBody', 'Logs and terminal output will appear here.')}
				/>
			</div>
		</section>
	);
}
