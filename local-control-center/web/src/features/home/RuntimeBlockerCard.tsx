/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { ArrowRight, PlugZap } from 'lucide-react';

import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { CardHead, HomeCard } from './HomeCardShell';
import type { HomeProvider } from './homeModel';

/**
 * Gallery card for a runtime that cannot execute work yet. Shows the runtime
 * name and the reason it is blocked, and opens the runtime configuration on
 * click so the user can clear the blocker.
 */
export function RuntimeBlockerCard({
	provider,
	onOpen,
}: {
	provider: HomeProvider;
	onOpen: () => void;
}) {
	const { t } = useI18n();
	const configureLabel = t('app.home.configure', 'Configure');

	return (
		<HomeCard
			kind="blocker"
			onClick={onOpen}
			ariaLabel={`${configureLabel}: ${provider.displayName}`}
		>
			<CardHead
				icon={<PlugZap size={15} aria-hidden="true" />}
				label={t('ui.static.runtime.c4740e4c', 'Runtime')}
				badge={
					<Badge tone={provider.configured ? 'warn' : 'danger'}>
						{provider.configured
							? t('app.home.notReady', 'Not ready')
							: t('app.home.notConfigured', 'Not configured')}
					</Badge>
				}
			/>
			<span className="home-card-title">{provider.displayName}</span>
			<span className="home-card-body">{provider.lastError || provider.reason}</span>
			<span className="home-card-cta">
				{configureLabel}
				<ArrowRight size={14} aria-hidden="true" />
			</span>
		</HomeCard>
	);
}
