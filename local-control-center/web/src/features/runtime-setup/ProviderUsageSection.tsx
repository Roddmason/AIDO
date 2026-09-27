/**
 * Usage quota of one provider inside its Providers & CLI card: one meter per observed window (5 h,
 * weekly, daily, monthly) with the suspension threshold marked on it, the "Suspended until" state
 * with a manual resume, and the operator's per-provider threshold (or "inherit the general one").
 * A provider over its threshold is skipped by AIDO even mid-loop, so the card must say so plainly.
 * @author Rodrigo Mason
 */

import { Gauge, RefreshCw } from 'lucide-react';
import { useEffect, useId, useState } from 'react';

import {
	type ProviderUsage,
	type ProviderUsageEntry,
	putProviderUsagePolicy,
	resumeProviderUsage,
} from '../../api/client';
import { StatusChip as Badge, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';

type UsageWindow = NonNullable<ProviderUsageEntry['windows']>[number];

/** Whether the card has anything to show: a remote usage source, an observed window or a suspension. */
export function hasUsageToShow(entry: ProviderUsageEntry | undefined): entry is ProviderUsageEntry {
	return Boolean(
		entry && (entry.hasRemoteSource || (entry.windows?.length ?? 0) > 0 || entry.suspended),
	);
}

/** Short local date for resets ("Sep 29, 07:39"); the full timestamp stays in the `title`. */
function formatReset(value: string | null | undefined, emptyLabel: string): string {
	if (!value) return emptyLabel;
	const parsed = Date.parse(value);
	if (Number.isNaN(parsed)) return value;
	return new Date(parsed).toLocaleString(undefined, {
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit',
	});
}

function meterTone(used: number | null | undefined, threshold: number): 'ok' | 'warn' | 'danger' {
	if (used === null || used === undefined) return 'ok';
	if (used >= threshold) return 'danger';
	return used >= threshold * 0.85 ? 'warn' : 'ok';
}

export function ProviderUsageSection({
	entry,
	generalThresholdPercent,
	token,
	busy,
	onChanged,
	onRefresh,
}: {
	entry: ProviderUsageEntry;
	generalThresholdPercent: number;
	token: string;
	/** Another card action is running; the usage controls wait for it like the rest of the card. */
	busy: boolean;
	onChanged: (next: ProviderUsage) => void;
	onRefresh: () => Promise<void>;
}) {
	const { t } = useI18n();
	const { notify } = useToast();
	const inputId = useId();
	const [draft, setDraft] = useState(
		entry.ownThresholdPercent == null ? '' : String(entry.ownThresholdPercent),
	);
	const [saving, setSaving] = useState<'policy' | 'resume' | 'refresh' | null>(null);
	useEffect(() => {
		setDraft(entry.ownThresholdPercent == null ? '' : String(entry.ownThresholdPercent));
	}, [entry.ownThresholdPercent]);

	const threshold = entry.thresholdPercent;
	const windows = entry.windows ?? [];
	const ownThreshold = entry.ownThresholdPercent ?? null;
	const parsed = draft.trim() === '' ? null : Number(draft);
	const draftValid = parsed === null || (Number.isFinite(parsed) && parsed >= 1 && parsed <= 100);
	const draftChanged = parsed !== ownThreshold;
	const disabled = busy || saving !== null;

	const run = async (kind: 'policy' | 'resume', call: () => Promise<ProviderUsage>) => {
		if (!token) {
			notify({
				title: t(
					'app.runtime.setup.tokenRequired',
					'A local write token is required to run runtime setup checks.',
				),
				tone: 'warn',
			});
			return;
		}
		setSaving(kind);
		try {
			onChanged(await call());
			notify({
				title:
					kind === 'resume'
						? t('app.providers.usage.resumed', 'Provider resumed until its usage window resets')
						: t('app.providers.usage.thresholdSaved', 'Usage threshold saved'),
				tone: 'ok',
			});
		} catch (error) {
			notify({
				title: t('app.providers.usage.saveFailed', 'Could not update the usage quota'),
				body: redactVisibleSecret(
					error instanceof Error ? error.message : String(error),
					'usage update failed',
				),
				tone: 'danger',
			});
		} finally {
			setSaving(null);
		}
	};

	const refresh = async () => {
		setSaving('refresh');
		try {
			await onRefresh();
		} finally {
			setSaving(null);
		}
	};

	const windowLabel = (window: UsageWindow) =>
		t(`app.providers.usage.window.${window.window}`, window.label);

	return (
		<section
			className="provider-usage"
			data-suspended={entry.suspended ? 'true' : undefined}
			aria-label={t('app.providers.usage.title', 'Usage quota')}
		>
			<div className="provider-usage-head">
				<span className="field-label provider-usage-title">
					<Gauge aria-hidden="true" size={13} />
					{t('app.providers.usage.title', 'Usage quota')}
				</span>
				{entry.suspended ? (
					<Badge tone="danger" className="provider-usage-chip">
						{t('app.providers.usage.suspendedUntil', 'Suspended until {time}').replace(
							'{time}',
							formatReset(entry.suspendedUntil, t('app.providers.usage.nextReset', 'next reset')),
						)}
					</Badge>
				) : null}
			</div>
			{entry.suspended ? (
				<p className="field-help provider-usage-note">
					{t(
						'app.providers.usage.suspendedHelp',
						'AIDO skips this provider, even inside a running loop, and fails over to the next runtime of each role.',
					)}
				</p>
			) : null}
			{windows.length ? (
				<ul className="provider-usage-windows">
					{windows.map((window) => {
						const used = window.usedPercent;
						const tone = window.status === 'rejected' ? 'danger' : meterTone(used, threshold);
						const label = windowLabel(window);
						return (
							<li key={window.window} className="provider-usage-window">
								<div className="provider-usage-row">
									<span>{label}</span>
									<span className="tnum">
										{used === null || used === undefined
											? t('app.providers.usage.exhausted', 'limit reached')
											: `${Math.round(used)}%`}
									</span>
								</div>
								{/* biome-ignore lint/a11y/useSemanticElements: a native <meter> cannot carry the threshold marker and styles differently per engine; role="meter" keeps the same semantics. */}
								<div
									className="provider-usage-meter"
									role="meter"
									aria-label={label}
									aria-valuemin={0}
									aria-valuemax={100}
									aria-valuenow={used ?? 100}
									aria-valuetext={t(
										'app.providers.usage.meterText',
										'{used} used, suspends at {threshold}%',
									)
										.replace(
											'{used}',
											used === null || used === undefined ? '100%' : `${Math.round(used)}%`,
										)
										.replace('{threshold}', String(Math.round(threshold)))}
									data-tone={tone}
								>
									<span
										className="provider-usage-fill"
										style={{ inlineSize: `${Math.min(100, Math.max(0, used ?? 100))}%` }}
									/>
									<span
										className="provider-usage-threshold"
										style={{ insetInlineStart: `${Math.min(100, Math.max(0, threshold))}%` }}
										aria-hidden="true"
									/>
								</div>
								{window.resetsAt ? (
									<span className="field-help">
										{t('app.providers.usage.resetsAt', 'Resets {time}').replace(
											'{time}',
											formatReset(window.resetsAt, ''),
										)}
									</span>
								) : null}
							</li>
						);
					})}
				</ul>
			) : (
				<p className="field-help provider-usage-note">
					{entry.hasRemoteSource
						? t(
								'app.providers.usage.notReadYet',
								'Usage not read yet. AIDO reads it from the local CLI login every few minutes.',
							)
						: t('app.providers.usage.none', 'No usage recorded yet.')}
				</p>
			)}
			{entry.lastPollError ? (
				<p className="field-help provider-usage-note" data-tone="warn">
					{redactVisibleSecret(entry.lastPollError, 'usage read failed')}
				</p>
			) : null}
			<div className="provider-usage-controls">
				<label className="provider-usage-threshold-field" htmlFor={inputId}>
					<span className="field-label provider-usage-label">
						{t('app.providers.usage.threshold', 'Suspend at')}
					</span>
					<span className="provider-usage-input">
						<input
							id={inputId}
							className="input tnum"
							type="number"
							inputMode="numeric"
							min={1}
							max={100}
							step={1}
							value={draft}
							placeholder={String(Math.round(generalThresholdPercent))}
							aria-invalid={draftValid ? undefined : 'true'}
							aria-describedby={`${inputId}-help`}
							disabled={disabled}
							onChange={(event) => setDraft(event.target.value)}
						/>
						<span aria-hidden="true">%</span>
					</span>
				</label>
				<button
					className="button"
					type="button"
					disabled={disabled || !draftValid || !draftChanged}
					aria-busy={saving === 'policy'}
					onClick={() =>
						void run('policy', () => putProviderUsagePolicy(token, entry.providerId, parsed))
					}
				>
					{t('app.providers.usage.saveThreshold', 'Save')}
				</button>
				{entry.suspended ? (
					<button
						className="button"
						type="button"
						disabled={disabled}
						aria-busy={saving === 'resume'}
						onClick={() => void run('resume', () => resumeProviderUsage(token, entry.providerId))}
					>
						{t('app.providers.usage.resume', 'Resume until reset')}
					</button>
				) : null}
				{entry.hasRemoteSource ? (
					<button
						className="button"
						type="button"
						disabled={disabled || !token}
						aria-busy={saving === 'refresh'}
						onClick={() => void refresh()}
					>
						<RefreshCw aria-hidden="true" size={14} />
						{t('app.providers.usage.refresh', 'Read usage')}
					</button>
				) : null}
			</div>
			<span className="field-help" id={`${inputId}-help`}>
				{draftValid
					? entry.thresholdSource === 'provider'
						? t(
								'app.providers.usage.ownThreshold',
								'Own threshold. Clear the field to use the general {general}%.',
							).replace('{general}', String(Math.round(generalThresholdPercent)))
						: t(
								'app.providers.usage.inheritsThreshold',
								'Uses the general {general}% (Settings > Runtime). Type a value to override it.',
							).replace('{general}', String(Math.round(generalThresholdPercent)))
					: t('app.providers.usage.thresholdInvalid', 'Enter a whole number from 1 to 100.')}
			</span>
			{entry.lastPollAt ? (
				<span className="field-help">
					{t('app.providers.usage.lastRead', 'Last read {time}').replace(
						'{time}',
						formatReset(entry.lastPollAt, ''),
					)}
				</span>
			) : null}
		</section>
	);
}
