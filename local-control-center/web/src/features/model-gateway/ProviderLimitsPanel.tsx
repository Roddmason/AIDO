/**
 * Configurable endpoint/model quota policies. These are local authoritative
 * guardrails; provider-observed cooldowns update the same policy state.
 * @author Rodrigo Mason
 */
import { useEffect, useMemo, useState } from 'react';

import {
	createModelGatewayProviderLimit,
	getModelGatewayProviderLimitStatus,
	patchModelGatewayProviderLimit,
} from '../../api/client';
import type {
	ModelGatewayProviderAccount,
	ModelGatewayProviderLimit,
	ModelGatewayProviderLimitStatus,
	ModelGatewayProviderLimitUpsert,
} from '../../api/types';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import { Button, Checkbox, SelectField, TextField } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { money, text } from './utils';

type UnknownLimitStrategy = NonNullable<ModelGatewayProviderLimitUpsert['unknownLimitStrategy']>;

type NumericField =
	| 'rpm'
	| 'tpm'
	| 'dailyRequests'
	| 'dailyTokens'
	| 'monthlyRequests'
	| 'monthlyTokens'
	| 'monthlyBudgetUsd'
	| 'maxCostPerRequestUsd'
	| 'maxConcurrency';

const EMPTY_NUMERIC_FIELDS: Record<NumericField, string> = {
	rpm: '',
	tpm: '',
	dailyRequests: '',
	dailyTokens: '',
	monthlyRequests: '',
	monthlyTokens: '',
	monthlyBudgetUsd: '',
	maxCostPerRequestUsd: '',
	maxConcurrency: '',
};

function browserTimezone(): string {
	try {
		return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
	} catch {
		return 'UTC';
	}
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function numericText(value: number | null | undefined): string {
	return value === null || value === undefined ? '' : String(value);
}

function optionalNumber(value: string, name: string, integer: boolean): number | null {
	const candidate = value.trim();
	if (!candidate) return null;
	const parsed = Number(candidate);
	if (!Number.isFinite(parsed) || (integer && !Number.isInteger(parsed))) {
		throw new Error(`${name} must be ${integer ? 'a whole number' : 'a number'}.`);
	}
	if (parsed < 0 || (integer && parsed === 0)) {
		throw new Error(`${name} must be ${integer ? 'greater than zero' : 'zero or greater'}.`);
	}
	return parsed;
}

export function ProviderLimitsPanel({
	providerLimits,
	providers,
	token,
	onSaved,
}: {
	providerLimits: ModelGatewayProviderLimit[];
	providers: ModelGatewayProviderAccount[];
	token: string;
	onSaved: () => void;
}) {
	const { t } = useI18n();
	const providerIds = useMemo(
		() =>
			Array.from(
				new Set([
					...providers.map((provider) => provider.providerId),
					...providerLimits.map((limit) => limit.providerId),
				]),
			).sort(),
		[providerLimits, providers],
	);
	const [editingId, setEditingId] = useState<string | null>(null);
	const [providerId, setProviderId] = useState(providerIds[0] ?? 'nvidia-nim-chat');
	const [model, setModel] = useState('*');
	const [numericFields, setNumericFields] = useState<Record<NumericField, string>>({
		...EMPTY_NUMERIC_FIELDS,
	});
	const [windowTimezone, setWindowTimezone] = useState(browserTimezone);
	const [fallbackRetryAfterSeconds, setFallbackRetryAfterSeconds] = useState('300');
	const [unknownLimitStrategy, setUnknownLimitStrategy] =
		useState<UnknownLimitStrategy>('conservative');
	const [enabled, setEnabled] = useState(true);
	const [busy, setBusy] = useState(false);
	const [statusBusy, setStatusBusy] = useState(false);
	const [status, setStatus] = useState<ModelGatewayProviderLimitStatus | null>(null);
	const [error, setError] = useState('');
	const [success, setSuccess] = useState('');

	useEffect(() => {
		if (editingId || providerIds.length === 0 || providerIds.includes(providerId)) return;
		setProviderId(providerIds[0]);
	}, [editingId, providerId, providerIds]);

	const setNumericField = (field: NumericField, value: string) => {
		setNumericFields((current) => ({ ...current, [field]: value }));
	};

	const reset = () => {
		setEditingId(null);
		setProviderId(providerIds[0] ?? 'nvidia-nim-chat');
		setModel('*');
		setNumericFields({ ...EMPTY_NUMERIC_FIELDS });
		setWindowTimezone(browserTimezone());
		setFallbackRetryAfterSeconds('300');
		setUnknownLimitStrategy('conservative');
		setEnabled(true);
		setStatus(null);
		setError('');
		setSuccess('');
	};

	const edit = (row: ModelGatewayProviderLimit) => {
		setEditingId(row.id);
		setProviderId(row.providerId);
		setModel(row.model);
		setNumericFields({
			rpm: numericText(row.rpm),
			tpm: numericText(row.tpm),
			dailyRequests: numericText(row.dailyRequests),
			dailyTokens: numericText(row.dailyTokens),
			monthlyRequests: numericText(row.monthlyRequests),
			monthlyTokens: numericText(row.monthlyTokens),
			monthlyBudgetUsd: numericText(row.monthlyBudgetUsd),
			maxCostPerRequestUsd: numericText(row.maxCostPerRequestUsd),
			maxConcurrency: numericText(row.maxConcurrency),
		});
		setWindowTimezone(row.windowTimezone);
		setFallbackRetryAfterSeconds(String(row.fallbackRetryAfterSeconds));
		setUnknownLimitStrategy(row.unknownLimitStrategy as UnknownLimitStrategy);
		setEnabled(row.enabled);
		setStatus(null);
		setError('');
		setSuccess('');
	};

	const payload = (): ModelGatewayProviderLimitUpsert => {
		if (!providerId.trim() || !model.trim()) {
			throw new Error('Provider and model are required. Use * for a provider-wide policy.');
		}
		if (!windowTimezone.trim()) throw new Error('Enter a valid IANA timezone.');
		const retryAfter = Number(fallbackRetryAfterSeconds);
		if (!Number.isInteger(retryAfter) || retryAfter < 0 || retryAfter > 86_400) {
			throw new Error('Fallback Retry-After must be a whole number from 0 to 86400.');
		}
		return {
			providerId: providerId.trim(),
			model: model.trim(),
			rpm: optionalNumber(numericFields.rpm, 'RPM', true),
			tpm: optionalNumber(numericFields.tpm, 'TPM', true),
			dailyRequests: optionalNumber(numericFields.dailyRequests, 'Daily requests', true),
			dailyTokens: optionalNumber(numericFields.dailyTokens, 'Daily tokens', true),
			monthlyRequests: optionalNumber(numericFields.monthlyRequests, 'Monthly requests', true),
			monthlyTokens: optionalNumber(numericFields.monthlyTokens, 'Monthly tokens', true),
			monthlyBudgetUsd: optionalNumber(numericFields.monthlyBudgetUsd, 'Monthly budget', false),
			maxCostPerRequestUsd: optionalNumber(
				numericFields.maxCostPerRequestUsd,
				'Max cost per request',
				false,
			),
			maxConcurrency: optionalNumber(numericFields.maxConcurrency, 'Max concurrency', true),
			windowTimezone: windowTimezone.trim(),
			enabled,
			fallbackRetryAfterSeconds: retryAfter,
			unknownLimitStrategy,
		};
	};

	const save = async () => {
		if (busy) return;
		setBusy(true);
		setError('');
		setSuccess('');
		try {
			const next = payload();
			if (editingId) {
				const { providerId: _providerId, model: _model, ...patch } = next;
				await patchModelGatewayProviderLimit(token, editingId, patch);
			} else {
				await createModelGatewayProviderLimit(token, next);
			}
			setSuccess(
				t(
					'app.providerLimits.success',
					'Limit policy saved. New requests will use the updated guardrail.',
				),
			);
			onSaved();
		} catch (saveError) {
			setError(errorMessage(saveError));
		} finally {
			setBusy(false);
		}
	};

	const previewStatus = async (row?: ModelGatewayProviderLimit) => {
		if (statusBusy) return;
		const targetProvider = row?.providerId ?? providerId.trim();
		const targetModel = row?.model ?? model.trim();
		if (!targetProvider || !targetModel) {
			setError(
				t(
					'app.modelGateway.limits.previewRequiresProviderModel',
					'Provider and model are required to preview effective status.',
				),
			);
			return;
		}
		setStatusBusy(true);
		setError('');
		try {
			const result = await getModelGatewayProviderLimitStatus({
				providerId: targetProvider,
				model: targetModel,
			});
			setStatus(result.status);
		} catch (statusError) {
			setError(errorMessage(statusError));
		} finally {
			setStatusBusy(false);
		}
	};

	return (
		<PanelShell title={t('ui.static.provider.limits.7533783c', 'Provider Limits')}>
			<div className="stack">
				<p className="muted">
					{t(
						'app.providerLimits.help',
						'These endpoint/model limits are local guardrails. Leave unknown upstream limits unset and choose an explicit unknown-limit strategy; do not copy assumptions from a different NVIDIA endpoint.',
					)}
				</p>
				<DataTable
					rows={providerLimits}
					empty={
						<EmptyState
							title={t('ui.static.no.provider.limits.b14ad2ba', 'No provider limits')}
							body={t(
								'app.providerLimits.empty.body',
								'Create an explicit endpoint/model policy before enabling productive calls.',
							)}
						/>
					}
					columns={[
						{
							key: 'scope',
							label: t('app.providerLimits.scope', 'Endpoint / model'),
							render: (row) => (
								<div className="stack compact">
									<span className="mono">{text(row.providerId)}</span>
									<span>{text(row.model)}</span>
								</div>
							),
						},
						{ key: 'rpm', label: 'RPM', render: (row) => text(row.rpm) },
						{ key: 'tpm', label: 'TPM', render: (row) => text(row.tpm) },
						{
							key: 'daily',
							label: t('app.providerLimits.daily', 'Daily req / tokens'),
							render: (row) => `${text(row.dailyRequests)} / ${text(row.dailyTokens)}`,
						},
						{
							key: 'monthly',
							label: t('app.providerLimits.monthly', 'Monthly req / tokens'),
							render: (row) => `${text(row.monthlyRequests)} / ${text(row.monthlyTokens)}`,
						},
						{
							key: 'cost',
							label: t('app.providerLimits.cost', 'Monthly / request USD'),
							render: (row) =>
								`${money(row.monthlyBudgetUsd, 'unknown')} / ${money(
									row.maxCostPerRequestUsd,
									'unknown',
								)}`,
						},
						{
							key: 'concurrency',
							label: t('app.providerLimits.concurrency', 'Concurrency'),
							render: (row) => text(row.maxConcurrency),
						},
						{
							key: 'window',
							label: t('app.providerLimits.window', 'Window'),
							render: (row) => (
								<div className="stack compact">
									<span>{text(row.windowTimezone)}</span>
									<span className="muted">{text(row.unknownLimitStrategy)}</span>
								</div>
							),
						},
						{
							key: 'cooldown',
							label: t('ui.static.cooldown.98fd67d9', 'Cooldown'),
							render: (row) => text(row.cooldownUntil),
						},
						{
							key: 'enabled',
							label: t('ui.static.enabled.df174a3f', 'Enabled'),
							render: (row) => (
								<Badge tone={row.enabled ? 'ok' : 'warn'}>
									{row.enabled ? 'enabled' : 'disabled'}
								</Badge>
							),
						},
						{
							key: 'actions',
							label: t('ui.static.actions.c3cd636a', 'Actions'),
							render: (row) => (
								<div className="inline">
									<Button onClick={() => edit(row)}>{t('app.providerLimits.edit', 'Edit')}</Button>
									<Button loading={statusBusy} onClick={() => void previewStatus(row)}>
										{t('app.providerLimits.status', 'Status')}
									</Button>
								</div>
							),
						},
					]}
				/>

				<form
					className="form-grid"
					onSubmit={(event) => {
						event.preventDefault();
						void save();
					}}
				>
					<div className="inline">
						<Badge tone={editingId ? 'info' : 'ok'}>
							{editingId
								? t('app.providerLimits.editing', 'Editing existing policy')
								: t('app.providerLimits.creating', 'New policy')}
						</Badge>
						{editingId ? (
							<Button onClick={reset}>{t('app.providerLimits.cancelEdit', 'Cancel edit')}</Button>
						) : null}
					</div>
					<div className="grid three">
						<SelectField
							label={t('ui.static.provider.7ceee3f3', 'Provider')}
							value={providerId}
							onChange={(event) => setProviderId(event.target.value)}
							disabled={editingId !== null}
							required
						>
							{providerIds.length === 0 ? <option value={providerId}>{providerId}</option> : null}
							{providerIds.map((id) => (
								<option key={id} value={id}>
									{id}
								</option>
							))}
						</SelectField>
						<TextField
							label={t('ui.static.model.68c2cc7f', 'Model')}
							value={model}
							onChange={(event) => setModel(event.target.value)}
							disabled={editingId !== null}
							required
							help={t('app.providerLimits.modelHelp', 'Use * as the provider-wide fallback.')}
						/>
						<TextField
							label="RPM"
							type="number"
							min="1"
							value={numericFields.rpm}
							onChange={(event) => setNumericField('rpm', event.target.value)}
						/>
						<TextField
							label="TPM"
							type="number"
							min="1"
							value={numericFields.tpm}
							onChange={(event) => setNumericField('tpm', event.target.value)}
						/>
						<TextField
							label={t('ui.static.daily.requests.aa9a0d5f', 'Daily requests')}
							type="number"
							min="1"
							value={numericFields.dailyRequests}
							onChange={(event) => setNumericField('dailyRequests', event.target.value)}
						/>
						<TextField
							label={t('ui.static.daily.tokens.4a4f692d', 'Daily tokens')}
							type="number"
							min="1"
							value={numericFields.dailyTokens}
							onChange={(event) => setNumericField('dailyTokens', event.target.value)}
						/>
						<TextField
							label={t('app.providerLimits.monthlyRequests', 'Monthly requests')}
							type="number"
							min="1"
							value={numericFields.monthlyRequests}
							onChange={(event) => setNumericField('monthlyRequests', event.target.value)}
						/>
						<TextField
							label={t('app.providerLimits.monthlyTokens', 'Monthly tokens')}
							type="number"
							min="1"
							value={numericFields.monthlyTokens}
							onChange={(event) => setNumericField('monthlyTokens', event.target.value)}
						/>
						<TextField
							label={t('ui.static.monthly.budget.f260ddaf', 'Monthly budget')}
							type="number"
							min="0"
							step="0.000001"
							value={numericFields.monthlyBudgetUsd}
							onChange={(event) => setNumericField('monthlyBudgetUsd', event.target.value)}
						/>
						<TextField
							label={t('app.providerLimits.maxRequestCost', 'Max cost / request USD')}
							type="number"
							min="0"
							step="0.000001"
							value={numericFields.maxCostPerRequestUsd}
							onChange={(event) => setNumericField('maxCostPerRequestUsd', event.target.value)}
						/>
						<TextField
							label={t('app.providerLimits.maxConcurrency', 'Max concurrency')}
							type="number"
							min="1"
							value={numericFields.maxConcurrency}
							onChange={(event) => setNumericField('maxConcurrency', event.target.value)}
						/>
						<TextField
							label={t('app.providerLimits.timezone', 'Window timezone')}
							value={windowTimezone}
							onChange={(event) => setWindowTimezone(event.target.value)}
							required
							help={t(
								'app.providerLimits.timezoneHelp',
								'IANA name, for example America/Santiago.',
							)}
						/>
						<TextField
							label={t('app.providerLimits.retryAfter', 'Fallback Retry-After (seconds)')}
							type="number"
							min="0"
							max="86400"
							value={fallbackRetryAfterSeconds}
							onChange={(event) => setFallbackRetryAfterSeconds(event.target.value)}
						/>
						<SelectField
							label={t('ui.static.unknown.limit.strategy.c9e71d1c', 'Unknown limit strategy')}
							value={unknownLimitStrategy}
							onChange={(event) =>
								setUnknownLimitStrategy(event.target.value as UnknownLimitStrategy)
							}
						>
							<option value="conservative">conservative</option>
							<option value="block">block</option>
							<option value="allow">allow</option>
						</SelectField>
					</div>
					<Checkbox
						label={t('app.providerLimits.enabled', 'Enable this limit policy')}
						checked={enabled}
						onChange={(event) => setEnabled(event.target.checked)}
					/>
					{error ? (
						<div className="form-error" role="alert">
							{error}
						</div>
					) : null}
					{success ? (
						<div className="form-success" role="status">
							{success}
						</div>
					) : null}
					<div className="inline">
						<Button variant="primary" type="submit" loading={busy}>
							{editingId
								? t('app.providerLimits.saveEdit', 'Save policy')
								: t('app.providerLimits.create', 'Create policy')}
						</Button>
						<Button loading={statusBusy} onClick={() => void previewStatus()}>
							{t('app.providerLimits.preview', 'Preview effective status')}
						</Button>
					</div>
				</form>

				{status ? (
					<div className="stack" role="status" aria-live="polite">
						<div className="inline">
							<Badge tone={status.allowed ? 'ok' : 'warn'}>
								{status.allowed ? 'allowed' : 'blocked'}
							</Badge>
							<span className="mono">{status.providerId}</span>
							<span>{status.model}</span>
							<span>{status.reason}</span>
							<span>
								{t('app.providerLimits.activeLeases', 'Active leases')}: {status.activeLeases}
							</span>
							<span>
								{t('app.providerLimits.pressure', 'Pressure')}:{' '}
								{Math.round(status.quotaPressure * 100)}%
							</span>
						</div>
						<DataTable
							rows={status.windows}
							empty={
								<EmptyState
									title={t('app.providerLimits.noWindows', 'No active quota windows')}
									body={status.guarded ? status.reason : 'No enabled policy matched this scope.'}
								/>
							}
							columns={[
								{
									key: 'kind',
									label: t('app.modelGateway.limits.windowColumn', 'Window'),
									render: (row) => row.kind,
								},
								{
									key: 'requests',
									label: t(
										'app.modelGateway.limits.committedReservedRequests',
										'Committed / reserved requests',
									),
									render: (row) => `${row.committedRequests} / ${row.reservedRequests}`,
								},
								{
									key: 'tokens',
									label: t(
										'app.modelGateway.limits.committedReservedTokens',
										'Committed / reserved tokens',
									),
									render: (row) => `${row.committedTokens} / ${row.reservedTokens}`,
								},
								{
									key: 'unverified',
									label: t('app.modelGateway.limits.unverifiedTokens', 'Unverified tokens'),
									render: (row) => row.unverifiedTokens,
								},
								{
									key: 'reset',
									label: t('app.modelGateway.limits.resetsAt', 'Resets at'),
									render: (row) => row.resetsAt,
								},
							]}
						/>
					</div>
				) : null}
			</div>
		</PanelShell>
	);
}
