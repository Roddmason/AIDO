/**
 * Local endpoints section of Providers & CLI: one card per local OpenAI-compatible server (llama.cpp,
 * LM Studio, vLLM, generic) stating its name, server kind, base URL, health, where it runs, the model
 * currently loaded and how many models it serves, with probe, sync, edit and delete actions written
 * through the real control plane. Ollama endpoints keep their own panel. Deleting an endpoint that a
 * sealed thread team or a role policy still uses is refused by the backend with the references,
 * which the confirmation dialog lists instead of a generic failure.
 * @author Rodrigo Mason
 */

import { Pencil, RefreshCw, Server, ShieldCheck, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import {
	getLocalEndpoints,
	healthCheckModelGatewayProvider,
	type LocalEndpointView,
	patchLocalEndpoint,
	syncProviderAccountModels,
} from '../../api/client';
import {
	StatusChip as Badge,
	Button,
	Checkbox,
	IconButton,
	Dialog as Modal,
	TextField,
	Tooltip,
	useToast,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import { invalidateRuntimeTeamCache } from '../runtime-team/useRuntimeTeam';
import { DeleteEndpointDialog } from './EndpointDeleteDialog';
import {
	deriveLocalEndpointCard,
	errorMessage,
	isOllamaEndpoint,
	LOCALITY_META,
	type LocalEndpointCardModel,
} from './localEndpoints';
import { isAbsoluteHttpUrl, normalizeBaseUrl } from './ollamaEndpoints';
import { ProviderSwitch } from './ProviderSwitch';
import { healthStatusLabel } from './providerCardModel';
import { describeReason } from './reasonCopy';

type LocalEndpointsPanelProps = {
	token: string;
	/** Bumped by the parent after another surface (the setup wizard) wrote an endpoint. */
	revision?: number;
	onRefresh?: () => Promise<unknown> | undefined;
	/** Provider ids a running call holds; their switch shows "In use" and stays on. */
	inUseProviderIds?: ReadonlySet<string>;
};

type EndpointTask = 'probe' | 'sync';

export function LocalEndpointsPanel({
	token,
	revision = 0,
	onRefresh,
	inUseProviderIds,
}: LocalEndpointsPanelProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [endpoints, setEndpoints] = useState<LocalEndpointView[]>([]);
	const [loadError, setLoadError] = useState<string | null>(null);
	const [loaded, setLoaded] = useState(false);
	const [loading, setLoading] = useState(false);
	const [busyAction, setBusyAction] = useState<string | null>(null);
	const [editTarget, setEditTarget] = useState<LocalEndpointView | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<LocalEndpointCardModel | null>(null);

	const load = useCallback(async () => {
		setLoading(true);
		try {
			const payload = await getLocalEndpoints();
			setEndpoints(payload.endpoints.filter((endpoint) => !isOllamaEndpoint(endpoint)));
			setLoadError(null);
		} catch (error) {
			setLoadError(errorMessage(error));
		} finally {
			setLoaded(true);
			setLoading(false);
		}
	}, []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: revision is a deliberate refresh signal from the parent after a wizard save.
	useEffect(() => {
		void load();
	}, [load, revision]);

	const cards = useMemo(() => endpoints.map(deriveLocalEndpointCard), [endpoints]);

	const requireToken = (): boolean => {
		if (token) return true;
		notify({
			title: t(
				'app.localRuntime.panel.tokenRequired',
				'A local write token is required to change local endpoints.',
			),
			tone: 'warn',
		});
		return false;
	};

	const afterMutation = async () => {
		await Promise.all([load(), onRefresh?.()]);
	};

	/** Operator switch: an endpoint switched off is used by no thread, agent or failover. */
	const toggleEndpoint = async (endpointId: string, enabled: boolean) => {
		if (busyAction || !requireToken()) return;
		setBusyAction(`${endpointId}:toggle`);
		try {
			await patchLocalEndpoint(token, endpointId, { enabled });
			// The switch changes every scope's AI team: drop the cached teams.
			invalidateRuntimeTeamCache();
			notify({
				title: enabled
					? t('app.providers.action.enabled', 'Provider enabled for threads')
					: t('app.providers.action.disabled', 'Provider disabled for threads'),
				tone: 'ok',
			});
			await afterMutation();
		} catch (error) {
			notify({
				title: t('app.providers.action.toggleFailed', 'Could not update the provider'),
				body: redactVisibleSecret(errorMessage(error), 'local endpoint update failed'),
				tone: 'danger',
			});
		} finally {
			setBusyAction(null);
		}
	};

	const runTask = async (endpointId: string, task: EndpointTask) => {
		if (busyAction || !requireToken()) return;
		setBusyAction(`${endpointId}:${task}`);
		try {
			if (task === 'probe') {
				const { health } = await healthCheckModelGatewayProvider(token, endpointId);
				const healthy = health.healthStatus === 'healthy';
				const loadingModel = health.healthStatus === 'model_loading';
				notify({
					title: healthy
						? t('app.localRuntime.panel.probeOk', 'Endpoint responded')
						: loadingModel
							? t('app.localRuntime.panel.probeLoading', 'The server is loading a model')
							: t('app.localRuntime.panel.probeFailed', 'Endpoint is not ready'),
					body: describeReason(
						redactVisibleSecret(health.lastError || health.message, health.healthStatus),
						t,
					),
					tone: healthy ? 'ok' : loadingModel ? 'warn' : 'danger',
				});
			} else {
				const result = await syncProviderAccountModels(token, endpointId);
				notify({
					title: t('app.localRuntime.panel.modelsSynced', 'Models synced'),
					body: `${(result as { models?: unknown[] }).models?.length ?? 0} ${t('app.localRuntime.discovery.models', 'models listed')}`,
					tone: 'ok',
				});
			}
			await afterMutation();
		} catch (error) {
			notify({
				title: t('app.localRuntime.panel.actionFailed', 'Endpoint action failed'),
				body: redactVisibleSecret(errorMessage(error), 'local endpoint action failed'),
				tone: 'danger',
			});
			await load();
		} finally {
			setBusyAction(null);
		}
	};

	return (
		<section
			className="stack providers-cli-section"
			aria-label={t('app.localRuntime.panel.title', 'Local endpoints')}
		>
			<div className="surface-toolbar">
				<div className="inline">
					<Server aria-hidden="true" size={16} />
					<strong>{t('app.localRuntime.panel.title', 'Local endpoints')}</strong>
					<span className="muted">
						<span className="tnum">{cards.length}</span>{' '}
						{t('app.localRuntime.panel.configured', 'configured')}
					</span>
				</div>
			</div>
			<p className="muted">
				{t(
					'app.localRuntime.panel.summary',
					'llama.cpp, LM Studio, vLLM and other OpenAI-compatible servers on this machine or declared local. Add one with Add provider or Detect local runtimes.',
				)}
			</p>

			{loadError ? (
				<section className="empty-state" role="alert">
					<strong>
						{t('app.localRuntime.panel.loadFailed', 'Could not load local endpoints')}
					</strong>
					<span className="field-help">
						{redactVisibleSecret(
							loadError,
							t(
								'app.localRuntime.panel.loadFailedBody',
								'AIDO could not read the local endpoints.',
							),
						)}
					</span>
					<Button
						variant="primary"
						loading={loading}
						icon={<RefreshCw aria-hidden="true" size={14} />}
						onClick={() => void load()}
					>
						{t('app.global.retry', 'Retry')}
					</Button>
				</section>
			) : !loaded ? (
				<section className="empty-state" role="status">
					<strong>{t('app.localRuntime.panel.loading', 'Loading local endpoints…')}</strong>
				</section>
			) : cards.length === 0 ? (
				<section className="empty-state" aria-live="polite">
					<strong>
						{t('app.localRuntime.panel.empty', 'No local endpoint is configured yet')}
					</strong>
					<span className="field-help">
						{t(
							'app.localRuntime.panel.emptyHint',
							'Start llama.cpp, LM Studio or vLLM, then use Detect local runtimes to add it.',
						)}
					</span>
				</section>
			) : (
				<div className="masonry-grid provider-grid">
					{cards.map((card) => (
						<LocalEndpointCard
							key={card.id}
							card={card}
							busyAction={busyAction}
							inUse={inUseProviderIds?.has(card.id) ?? false}
							onToggleEnabled={(enabled) => void toggleEndpoint(card.id, enabled)}
							onProbe={() => void runTask(card.id, 'probe')}
							onSync={() => void runTask(card.id, 'sync')}
							onEdit={() =>
								setEditTarget(endpoints.find((endpoint) => endpoint.id === card.id) ?? null)
							}
							onDelete={() => {
								if (requireToken()) setDeleteTarget(card);
							}}
						/>
					))}
				</div>
			)}

			<EditEndpointDialog
				endpoint={editTarget}
				token={token}
				onClose={() => setEditTarget(null)}
				onSaved={async () => {
					setEditTarget(null);
					await afterMutation();
				}}
			/>
			<DeleteEndpointDialog
				card={deleteTarget}
				token={token}
				onClose={() => setDeleteTarget(null)}
				onDeleted={async () => {
					setDeleteTarget(null);
					invalidateRuntimeTeamCache();
					notify({
						title: t('app.localRuntime.panel.deleted', 'Local endpoint deleted'),
						tone: 'ok',
					});
					await afterMutation();
				}}
			/>
		</section>
	);
}

function LocalEndpointCard({
	card,
	busyAction,
	inUse,
	onToggleEnabled,
	onProbe,
	onSync,
	onEdit,
	onDelete,
}: {
	card: LocalEndpointCardModel;
	busyAction: string | null;
	inUse: boolean;
	onToggleEnabled: (enabled: boolean) => void;
	onProbe: () => void;
	onSync: () => void;
	onEdit: () => void;
	onDelete: () => void;
}) {
	const { t } = useI18n();
	const locality = LOCALITY_META[card.locality] ?? LOCALITY_META.remote;
	const anyBusy = busyAction !== null;
	const rawFailure = card.failureReason ? redactVisibleSecret(card.failureReason) : '';

	// Same anatomy as every provider card: title + switch in the header, state first in the meta row.
	// The switch already says Active/Inactive, so no separate enabled/disabled badge repeats it.
	return (
		<article className="card card--static provider-card" data-tone={card.healthTone}>
			<div className="card-header">
				<div className="inline">
					<Server aria-hidden="true" size={18} />
					<h4 className="card-title" title={card.displayName}>
						{card.displayName}
					</h4>
				</div>
				<div className="inline">
					<ProviderSwitch
						providerName={card.displayName}
						checked={card.enabled}
						busy={anyBusy}
						inUse={inUse}
						onChange={onToggleEnabled}
					/>
				</div>
			</div>

			<div className="card-meta">
				<Badge tone={card.healthTone}>{healthStatusLabel(card.healthStatus, t)}</Badge>
				<Badge tone="info">{card.serverLabel}</Badge>
				<Badge tone={locality.tone}>{t(locality.labelKey, locality.fallback)}</Badge>
				{card.hasCredential ? (
					<Badge tone="ok">
						<ShieldCheck aria-hidden="true" size={12} />
						<span>{t('app.localRuntime.panel.credential', 'bearer token')}</span>
					</Badge>
				) : null}
			</div>

			<dl className="provider-facts">
				<div>
					<dt>{t('app.localRuntime.panel.endpointId', 'Endpoint id')}</dt>
					<dd className="mono">{card.id}</dd>
				</div>
				<div>
					<dt>{t('app.localRuntime.panel.baseUrl', 'Base URL')}</dt>
					<dd className="mono">{card.baseUrl}</dd>
				</div>
				<div>
					<dt>{t('app.localRuntime.panel.loadedModel', 'Loaded model')}</dt>
					<dd className="mono">
						{!card.loadStateTracked
							? t('app.localRuntime.panel.loadNotTracked', 'not tracked')
							: card.loadedModels.length
								? card.loadedModels.join(', ')
								: t('app.localRuntime.panel.noLoadedModel', 'none loaded')}
					</dd>
				</div>
				<div>
					<dt>{t('app.localRuntime.panel.models', 'Models')}</dt>
					<dd className="tnum">{card.modelCount}</dd>
				</div>
			</dl>

			{rawFailure ? (
				<p className="card-body card-reason" title={rawFailure}>
					<span className="field-label">{t('app.localRuntime.panel.lastError', 'Last error')}</span>{' '}
					{describeReason(rawFailure, t)}
				</p>
			) : null}

			<div className="provider-card-actions">
				<Button
					className="endpoint-card-action"
					variant="primary"
					loading={busyAction === `${card.id}:probe`}
					disabled={anyBusy}
					onClick={onProbe}
				>
					{t('app.localRuntime.panel.probe', 'Probe')}
				</Button>
				<Button
					className="endpoint-card-action"
					loading={busyAction === `${card.id}:sync`}
					disabled={anyBusy}
					onClick={onSync}
				>
					{t('app.localRuntime.panel.sync', 'Sync models')}
				</Button>
				{/* Same row anatomy as the Ollama card: two labelled actions, then icon buttons. */}
				<span className="provider-card-icon-actions">
					<Tooltip label={t('app.localRuntime.panel.edit', 'Edit')}>
						<IconButton
							aria-label={t('app.localRuntime.panel.edit', 'Edit')}
							disabled={anyBusy}
							onClick={onEdit}
						>
							<Pencil aria-hidden="true" size={15} />
						</IconButton>
					</Tooltip>
					<Tooltip label={t('app.localRuntime.panel.delete', 'Delete')}>
						<IconButton
							className="provider-card-delete-icon"
							aria-label={t('app.localRuntime.panel.delete', 'Delete')}
							disabled={anyBusy}
							onClick={onDelete}
						>
							<Trash2 aria-hidden="true" size={15} />
						</IconButton>
					</Tooltip>
				</span>
			</div>
		</article>
	);
}

/** Edits the stored endpoint; an empty credential field keeps the stored reference. */
function EditEndpointDialog({
	endpoint,
	token,
	onClose,
	onSaved,
}: {
	endpoint: LocalEndpointView | null;
	token: string;
	onClose: () => void;
	onSaved: () => Promise<void>;
}) {
	const { t } = useI18n();
	const [displayName, setDisplayName] = useState('');
	const [baseUrl, setBaseUrl] = useState('');
	const [credentialRef, setCredentialRef] = useState('');
	const [enabled, setEnabled] = useState(true);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');

	useEffect(() => {
		if (!endpoint) return;
		setDisplayName(endpoint.displayName);
		setBaseUrl(endpoint.baseUrl);
		setCredentialRef('');
		setEnabled(endpoint.enabled);
		setError('');
	}, [endpoint]);

	if (!endpoint) return null;

	const submit = async () => {
		if (busy) return;
		if (!isAbsoluteHttpUrl(baseUrl)) {
			setError(
				t(
					'app.localRuntime.edit.errorBaseUrl',
					'Enter an absolute http(s) URL with no query string.',
				),
			);
			return;
		}
		setBusy(true);
		setError('');
		try {
			const ref = credentialRef.trim();
			await patchLocalEndpoint(token, endpoint.id, {
				baseUrl: normalizeBaseUrl(baseUrl),
				displayName: displayName.trim() || endpoint.id,
				enabled,
				...(ref ? { credentialRef: ref } : {}),
			});
			await onSaved();
		} catch (saveError) {
			setError(redactVisibleSecret(errorMessage(saveError), 'local endpoint update failed'));
		} finally {
			setBusy(false);
		}
	};

	return (
		<Modal open label={t('app.localRuntime.edit.title', 'Edit local endpoint')} onClose={onClose}>
			<div className="form-grid">
				<TextField
					label={t('app.localRuntime.edit.displayName', 'Display name')}
					value={displayName}
					autoComplete="off"
					onChange={(event) => setDisplayName(event.target.value)}
				/>
				<TextField
					label={t('app.localRuntime.edit.baseUrl', 'Base URL')}
					value={baseUrl}
					autoComplete="off"
					onChange={(event) => setBaseUrl(event.target.value)}
					help={t(
						'app.localRuntime.edit.baseUrlHelp',
						'Changing the URL resets the stored health until the next probe.',
					)}
				/>
				<TextField
					label={t('app.localRuntime.edit.credentialRef', 'Credential reference')}
					value={credentialRef}
					autoComplete="off"
					placeholder="env:llama_api_key"
					onChange={(event) => setCredentialRef(event.target.value)}
					help={
						endpoint.hasCredential
							? t(
									'app.localRuntime.edit.credentialKeep',
									'Leave empty to keep the stored credential.',
								)
							: t(
									'app.localRuntime.edit.credentialHelp',
									'Optional bearer token reference; the secret is never shown.',
								)
					}
				/>
				<Checkbox
					label={t('app.localRuntime.edit.enabled', 'Enable this endpoint')}
					checked={enabled}
					onChange={(event) => setEnabled(event.target.checked)}
				/>
				{error ? (
					<div className="form-error" role="alert">
						{error}
					</div>
				) : null}
				<div className="wizard-actions">
					<Button onClick={onClose} disabled={busy}>
						{t('app.localRuntime.edit.cancel', 'Cancel')}
					</Button>
					<Button variant="primary" loading={busy} onClick={() => void submit()}>
						{t('app.localRuntime.edit.save', 'Save changes')}
					</Button>
				</div>
			</div>
		</Modal>
	);
}
