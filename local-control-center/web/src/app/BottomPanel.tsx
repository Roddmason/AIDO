/**
 * Collapsible bottom dock for logs / run output.
 *
 * Presentational surface only: a titled, closable frame with a Logs/Output tablist.
 * Live content is wired in by the caller; until then each tab shows an empty state.
 * @author Rodrigo Mason
 */
import { Terminal, X } from 'lucide-react';
import { useState } from 'react';

import { EmptyState, IconButton, Tabs } from '../components/ui';
import { useI18n } from '../i18n/I18nProvider';

/** Titled, closable bottom dock with Logs/Output tabs; `onClose` collapses the pane. */
export function BottomPanel({ onClose }: { onClose: () => void }) {
	const { t } = useI18n();
	const title = t('app.bottomPanel.title', 'Bottom panel');
	const [activeTab, setActiveTab] = useState('logs');
	const tabs = [
		{ id: 'logs', label: t('app.bottomPanel.logs', 'Logs') },
		{ id: 'output', label: t('app.bottomPanel.output', 'Output') },
	];
	return (
		<section className="bottom-panel" aria-label={title}>
			<header className="bottom-panel-header">
				<span className="bottom-panel-title">
					<Terminal aria-hidden="true" size={15} />
					{title}
				</span>
				<IconButton aria-label={t('app.bottomPanel.close', 'Close bottom panel')} onClick={onClose}>
					<X aria-hidden="true" size={16} />
				</IconButton>
			</header>
			<Tabs
				className="bottom-panel-tabs"
				idBase="bottom-dock"
				label={title}
				tabs={tabs}
				activeTab={activeTab}
				onChange={setActiveTab}
			>
				<div className="bottom-panel-body">
					<EmptyState
						title={t('app.bottomPanel.emptyTitle', 'No active session')}
						body={t('app.bottomPanel.emptyBody', 'Logs and terminal output will appear here.')}
					/>
				</div>
			</Tabs>
		</section>
	);
}
