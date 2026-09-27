/**
 * Home gallery cards for runtimes. `RuntimeBlockerCard` is a runtime the operator enabled (or a
 * thread is using) that cannot execute yet: a warning with its reason and a way to fix it.
 * `SetupRuntimesCard` is the single neutral call to action shown while no runtime can run at all;
 * a provider nobody set up is not an error, so it never gets a card or a danger tone of its own.
 * @author Rodrigo Mason
 */
import { ArrowRight, PlugZap } from 'lucide-react';

import { StatusChip as Badge } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { CardHead, HomeCard } from './HomeCardShell';
import type { HomeProvider } from './homeModel';

/** An enabled runtime that cannot execute work yet; opens the runtime configuration. */
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
				badge={<Badge tone="warn">{t('app.home.notReady', 'Not ready')}</Badge>}
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

/** One neutral card while nothing can run: how many providers are available to set up. */
export function SetupRuntimesCard({ notSetUp, onOpen }: { notSetUp: number; onOpen: () => void }) {
	const { t } = useI18n();
	const setUpLabel = t('app.home.setUpRuntimes', 'Set up runtimes');
	const title = t('app.home.noRuntimeReady', 'No runtime can run work yet');

	return (
		<HomeCard kind="setup" onClick={onOpen} ariaLabel={`${setUpLabel}: ${title}`}>
			<CardHead
				icon={<PlugZap size={15} aria-hidden="true" />}
				label={t('ui.static.runtimes.8fb69b37', 'Runtimes')}
				badge={null}
			/>
			<span className="home-card-title">{title}</span>
			<span className="home-card-body">
				{t('app.home.availableToSetUp', '{count} providers available to set up').replace(
					'{count}',
					String(notSetUp),
				)}
			</span>
			<span className="home-card-cta">
				{setUpLabel}
				<ArrowRight size={14} aria-hidden="true" />
			</span>
		</HomeCard>
	);
}
