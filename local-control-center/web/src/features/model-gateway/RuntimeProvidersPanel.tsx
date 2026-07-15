/**
 * Runtime & Model Gateway provider status table. Read surface for discovered runtime providers:
 * detection/configuration/availability/executable state, health status, sanitized configuration
 * variables and the per-runtime healthcheck action. The primary view stays narrow; advanced
 * columns (capabilities, last error, version, detected command) are revealed on demand through the
 * column chooser. Secrets are always passed through `redactVisibleSecret`.
 * @author Rodrigo Mason
 */
import type { ReactNode } from 'react';

import type { RuntimeProvider, RuntimeProviderConfiguration } from '../../api/types';
import { StatusChip as Badge, DataTable, EmptyState, Surface } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import { type AdvancedColumn, ColumnChooser, useColumnVisibility } from './ColumnChooser';
import { text } from './utils';

type Column = { key: string; label: string; render: (row: RuntimeProvider) => ReactNode };

export function RuntimeProvidersPanel({
	runtimeRows,
	runtimeConfigurationById,
	busyAction,
	onRefreshHealth,
}: {
	runtimeRows: RuntimeProvider[];
	runtimeConfigurationById: Map<string, RuntimeProviderConfiguration>;
	busyAction: string;
	onRefreshHealth: (runtime: RuntimeProvider) => void;
}) {
	const { t } = useI18n();
	const { visible, toggle } = useColumnVisibility('runtime-providers');
	const runtimeStateBadge = (enabled: boolean, positive: string, negative: string) => (
		<Badge tone={enabled ? 'ok' : 'warn'}>{enabled ? positive : negative}</Badge>
	);

	const primaryLead: Column[] = [
		{
			key: 'id',
			label: t('ui.static.id.87ea5dfc', 'Id'),
			render: (row) => <span className="mono">{row.id}</span>,
		},
		{
			key: 'kind',
			label: t('ui.static.kind.e00ac23f', 'Kind'),
			render: (row) => <Badge>{row.kind}</Badge>,
		},
		{
			key: 'detected',
			label: t('app.workspace.detection.found', 'Detected'),
			render: (row) =>
				runtimeStateBadge(
					Boolean(row.detected),
					t('app.modelGateway.runtime.detected', 'detected'),
					t('app.modelGateway.runtime.notDetected', 'not detected'),
				),
		},
		{
			key: 'configured',
			label: t('ui.static.configured.7bde0f0a', 'Configured'),
			render: (row) =>
				runtimeStateBadge(
					Boolean(row.configured),
					t('app.modelGateway.runtime.configured', 'configured'),
					t('app.modelGateway.runtime.notConfigured', 'not configured'),
				),
		},
		{
			key: 'available',
			label: t('ui.static.available.78945de8', 'Available'),
			render: (row) =>
				runtimeStateBadge(
					Boolean(row.available),
					t('app.modelGateway.runtime.available', 'available'),
					t('app.modelGateway.runtime.notAvailable', 'not available'),
				),
		},
		{
			key: 'executable',
			label: t('ui.static.executable.6f703eda', 'Executable'),
			render: (row) =>
				runtimeStateBadge(
					Boolean(row.executable),
					t('app.modelGateway.runtime.executable', 'executable'),
					t('app.modelGateway.runtime.notExecutable', 'not executable'),
				),
		},
		{
			key: 'healthStatus',
			label: t('ui.static.health.status.a4a97cf0', 'Health status'),
			render: (row) => (
				<Badge tone={row.healthStatus === 'healthy' ? 'ok' : 'warn'}>
					{redactVisibleSecret(row.healthStatus, t('app.runtime.card.unknown', 'unknown'))}
				</Badge>
			),
		},
		{
			key: 'configuration',
			label: t('ui.static.configuration.8ce677fb', 'Configuration'),
			render: (row) => {
				const configuration = runtimeConfigurationById.get(String(row.id ?? ''));
				const configured = configuration?.configured === true || row.configured;
				const missingFromConfiguration = Array.isArray(configuration?.missing)
					? configuration.missing
					: [];
				const missingSource =
					missingFromConfiguration.length || configured
						? missingFromConfiguration
						: (row.requiredConfiguration ?? []);
				const missing = missingSource.map((item) => redactVisibleSecret(item)).filter(Boolean);
				const variables = Array.isArray(configuration?.variables) ? configuration.variables : [];
				return (
					<div className="stack">
						<Badge tone={configured ? 'ok' : 'warn'}>
							{text(
								configuration?.status,
								configured
									? t('app.modelGateway.runtime.configured', 'configured')
									: 'missing_config',
							)}
						</Badge>
						<div className="inline">
							{missing.length ? (
								missing.map((item) => (
									<Badge key={item} tone="warn">
										{item}
									</Badge>
								))
							) : (
								<Badge tone="ok">{t('app.modelGateway.runtime.noneMissing', 'none missing')}</Badge>
							)}
						</div>
						{variables.map((variable) => {
							const name = redactVisibleSecret(variable.name);
							const fingerprint = redactVisibleSecret(variable.fingerprint, '');
							return (
								<div className="inline" key={name}>
									<Badge tone={variable.configured ? 'ok' : 'warn'}>
										{variable.configured
											? t('app.modelGateway.runtime.variableSet', 'set')
											: t('app.runtime.card.missing', 'missing')}
									</Badge>
									<span className="mono">{name}</span>
									{fingerprint ? <span className="mono">{fingerprint}</span> : null}
								</div>
							);
						})}
					</div>
				);
			},
		},
		{
			key: 'reason',
			label: t('ui.static.reason.f219cc06', 'Reason'),
			render: (row) => redactVisibleSecret(row.reason),
		},
	];

	const advanced: AdvancedColumn[] = [
		{ key: 'capabilities', label: t('ui.static.capabilities.ca09c54b', 'Capabilities') },
		{ key: 'lastError', label: t('ui.static.last.error.5e4df866', 'Last error') },
		{ key: 'version', label: t('ui.static.version.2da600bf', 'Version') },
		{ key: 'command', label: t('ui.static.detected.command.c696971c', 'Detected command') },
	];
	const advancedColumns: Record<string, Column> = {
		capabilities: {
			key: 'capabilities',
			label: t('ui.static.capabilities.ca09c54b', 'Capabilities'),
			render: (row) => (
				<div className="inline">
					{row.requiresApproval ? (
						<Badge tone="warn">{t('app.modelGateway.runtime.approvalBadge', 'approval')}</Badge>
					) : null}
					{row.capabilities?.length ? (
						row.capabilities.map((capability) => <Badge key={capability}>{capability}</Badge>)
					) : (
						<Badge tone="warn">{t('app.workspace.detection.none', 'none')}</Badge>
					)}
				</div>
			),
		},
		lastError: {
			key: 'lastError',
			label: t('ui.static.last.error.5e4df866', 'Last error'),
			render: (row) =>
				redactVisibleSecret(row.lastError, t('app.workspace.detection.none', 'none')),
		},
		version: {
			key: 'version',
			label: t('ui.static.version.2da600bf', 'Version'),
			render: (row) => <span className="mono">{redactVisibleSecret(row.version)}</span>,
		},
		command: {
			key: 'command',
			label: t('ui.static.detected.command.c696971c', 'Detected command'),
			render: (row) => <span className="mono">{redactVisibleSecret(row.detectedCommand)}</span>,
		},
	};
	const healthColumn: Column = {
		key: 'health',
		label: t('ui.static.healthcheck.b89e0ef6', 'Healthcheck'),
		render: (row) => {
			const runtimeId = String(row.id ?? '');
			const kind = String(row.kind ?? '');
			const canRefresh = kind === 'cli' || kind === 'api' || kind === 'gateway' || kind === 'local';
			const busy = busyAction === `${runtimeId}:runtime-health`;
			return (
				<div className="stack">
					<span className="mono">
						{redactVisibleSecret(
							row.healthCheckedAt,
							t('app.modelGateway.runtime.notChecked', 'not checked'),
						)}
					</span>
					<button
						className="button"
						type="button"
						disabled={!canRefresh || busy}
						aria-label={`${t('app.modelGateway.runtime.refreshHealthcheckFor', 'Refresh healthcheck for')} ${runtimeId}`}
						onClick={() => onRefreshHealth(row)}
					>
						{busy
							? t('app.modelGateway.runtime.refreshingHealthcheck', 'Refreshing healthcheck')
							: t('app.modelGateway.runtime.refreshHealthcheck', 'Refresh healthcheck')}
					</button>
					{canRefresh ? null : (
						<span className="muted">
							{t(
								'ui.static.no.automated.healthcheck.endpoint.f9c95375',
								'No automated healthcheck endpoint.',
							)}
						</span>
					)}
				</div>
			);
		},
	};

	const columns: Column[] = [
		...primaryLead,
		...advanced
			.filter((column) => visible.has(column.key))
			.map((column) => advancedColumns[column.key]),
		healthColumn,
	];
	const emptyRuntimeProviders = (
		<EmptyState
			title={t('ui.static.no.runtime.provider.status.70888a8d', 'No runtime provider status')}
			body={t(
				'ui.static.runtime.discovery.has.not.returned.provider.status.records.baf9776d',
				'Runtime discovery has not returned provider status records.',
			)}
		/>
	);

	return (
		<Surface title={t('ui.static.runtime.and.model.gateway.45cf9fb6', 'Runtime & Model Gateway')}>
			<h3 className="section-subtitle">
				{t('ui.static.runtime.providers.0acdc40d', 'Runtime Providers')}
			</h3>
			<ColumnChooser advanced={advanced} visible={visible} onToggle={toggle} />
			{runtimeRows.length ? (
				<DataTable rows={runtimeRows} empty={emptyRuntimeProviders} columns={columns} />
			) : (
				<div className="table-wrap">
					<table className="data-table">
						<thead>
							<tr>
								{columns.map((column) => (
									<th key={column.key} scope="col">
										{column.label}
									</th>
								))}
							</tr>
						</thead>
						<tbody>
							<tr>
								<td colSpan={columns.length}>{emptyRuntimeProviders}</td>
							</tr>
						</tbody>
					</table>
				</div>
			)}
		</Surface>
	);
}
