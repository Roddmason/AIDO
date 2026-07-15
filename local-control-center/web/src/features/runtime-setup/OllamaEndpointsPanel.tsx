/**
 * Ollama endpoints section of Providers & CLI: one card per configured endpoint — local or remote —
 * stating its base URL, health, last measured latency, synced models and whether it is enabled, plus
 * the four actions that keep it honest: add an endpoint, validate it against `/api/tags`, sync its
 * model catalog, and make it the preferred candidate of a role. Every action writes through the real
 * control plane and re-reads it; a failed probe surfaces the backend's reason instead of a green tick.
 * @author Rodrigo Mason
 */

import { Boxes, PlugZap, Plus, RefreshCw, Server, ShieldCheck, Wand2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import {
	createOllamaEndpoint,
	getModelGatewayRolePolicies,
	getOllamaEndpoints,
	healthCheckOllamaEndpoint,
	patchModelGatewayRolePolicy,
	syncOllamaEndpointModels,
} from '../../api/client';
import type { ModelGatewayRolePolicy, OllamaEndpoint } from '../../api/types';
import {
	StatusChip as Badge,
	Button,
	Checkbox,
	Dialog as Modal,
	SelectField,
	TextField,
	useToast,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import {
	deriveEndpointCard,
	type EndpointCardModel,
	isAbsoluteHttpUrl,
	isValidEndpointId,
	normalizeBaseUrl,
	preferredWithEndpointFirst,
} from './ollamaEndpoints';

/** Model chips shown inline before the card falls back to a count-only summary. */
const MODEL_CHIP_LIMIT = 6;

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

type EndpointsPanelProps = {
	token: string;
	onRefresh?: () => Promise<unknown> | undefined;
};

export function OllamaEndpointsPanel({ token, onRefresh }: EndpointsPanelProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [endpoints, setEndpoints] = useState<OllamaEndpoint[]>([]);
	const [rolePolicies, setRolePolicies] = useState<ModelGatewayRolePolicy[]>([]);
	const [busyAction, setBusyAction] = useState<string | null>(null);
	const [endpointLoadError, setEndpointLoadError] = useState<string | null>(null);
	const [endpointsLoaded, setEndpointsLoaded] = useState(false);
	const [endpointsLoading, setEndpointsLoading] = useState(false);
	const [addOpen, setAddOpen] = useState(false);
	const [roleTarget, setRoleTarget] = useState<EndpointCardModel | null>(null);

	const load = useCallback(async () => {
		setEndpointsLoading(true);
		const [endpointsPayload, rolePayload] = await Promise.allSettled([
			getOllamaEndpoints(),
			getModelGatewayRolePolicies(),
		]);
		if (endpointsPayload.status === 'fulfilled') {
			setEndpoints(endpointsPayload.value.endpoints);
			setEndpointLoadError(null);
		} else {
			setEndpointLoadError(errorMessage(endpointsPayload.reason));
		}
		setEndpointsLoaded(true);
		setEndpointsLoading(false);
		// Role policies only enrich the "preferred for" chips; losing them must not hide the cards.
		if (rolePayload.status === 'fulfilled') setRolePolicies(rolePayload.value.rolePolicies);
	}, []);

	useEffect(() => {
		void load();
	}, [load]);

	const cards = useMemo(
		() => endpoints.map((endpoint) => deriveEndpointCard(endpoint, rolePolicies)),
		[endpoints, rolePolicies],
	);

	const requireToken = (): boolean => {
		if (token) return true;
		notify({
			title: t(
				'app.ollama.tokenRequired',
				'A local write token is required to change Ollama endpoints.',
			),
			tone: 'warn',
		});
		return false;
	};

	const runEndpointTask = async (endpointId: string, task: 'validate' | 'sync') => {
		if (busyAction || !requireToken()) return;
		setBusyAction(`${endpointId}:${task}`);
		try {
			if (task === 'validate') {
				const response = await healthCheckOllamaEndpoint(token, endpointId);
				const health = response.health;
				const healthy = health.healthStatus === 'healthy';
				notify({
					title: healthy
						? t('app.ollama.action.validateOk', 'Endpoint responded')
						: t('app.ollama.action.validateFailed', 'Endpoint did not respond'),
					body: healthy
						? `${health.latencyMs} ms`
						: redactVisibleSecret(
								health.lastError || health.message,
								t('app.ollama.card.noReason', 'No reason reported.'),
							),
					tone: healthy ? 'ok' : 'danger',
				});
			} else {
				const response = await syncOllamaEndpointModels(token, endpointId);
				notify({
					title: t('app.ollama.action.modelsSynced', 'Models synced'),
					body: String(response.models.length),
					tone: 'ok',
				});
			}
			await Promise.all([load(), onRefresh?.()]);
		} catch (error) {
			notify({
				title: t('app.ollama.action.failed', 'Endpoint action failed'),
				body: redactVisibleSecret(errorMessage(error), 'ollama endpoint action failed'),
				tone: 'danger',
			});
			// A rejected probe still updates the stored health, so re-read before giving up.
			await load();
		} finally {
			setBusyAction(null);
		}
	};

	const setPreferredForRole = async (endpointId: string, role: string, model: string) => {
		if (busyAction || !requireToken()) return;
		const policy = rolePolicies.find((item) => item.role === role);
		if (!policy) return;
		setBusyAction(`${endpointId}:role`);
		try {
			await patchModelGatewayRolePolicy(token, policy.id, {
				preferred: preferredWithEndpointFirst(policy, endpointId, model),
			});
			notify({
				title: t('app.ollama.action.preferredSet', 'Role now prefers this endpoint'),
				body: `${role} · ${model}`,
				tone: 'ok',
			});
			setRoleTarget(null);
			await load();
		} catch (error) {
			notify({
				title: t('app.ollama.action.preferredFailed', 'Could not update the role policy'),
				body: redactVisibleSecret(errorMessage(error), 'role policy update failed'),
				tone: 'danger',
			});
		} finally {
			setBusyAction(null);
		}
	};

	return (
		<section className="stack">
			<div className="surface-toolbar">
				<div className="inline">
					<Boxes aria-hidden="true" size={16} />
					<strong>{t('app.ollama.title', 'Ollama endpoints')}</strong>
					<span className="muted">
						<span className="tnum">{cards.length}</span> {t('app.ollama.configured', 'configured')}
					</span>
				</div>
				<Button variant="primary" icon={<Plus size={15} />} onClick={() => setAddOpen(true)}>
					{t('app.ollama.addEndpoint', 'Add endpoint')}
				</Button>
			</div>
			<p className="muted">
				{t(
					'app.ollama.summary',
					'Run several Ollama servers side by side — the local daemon plus any remote host — and route each role to the one you trust.',
				)}
			</p>

			{endpointLoadError ? (
				<section className="empty-state" role="alert">
					<strong>{t('app.ollama.loadFailed', 'Could not load Ollama endpoints')}</strong>
					<span className="field-help">
						{redactVisibleSecret(
							endpointLoadError,
							t(
								'app.ollama.loadFailedBody',
								'AIDO could not read the configured endpoints. Retry before adding another one.',
							),
						)}
					</span>
					<Button
						variant="primary"
						loading={endpointsLoading}
						icon={<RefreshCw aria-hidden="true" size={14} />}
						onClick={() => void load()}
					>
						{t('app.global.retry', 'Retry')}
					</Button>
				</section>
			) : !endpointsLoaded ? (
				<section className="empty-state" role="status">
					<strong>{t('app.ollama.loading', 'Loading Ollama endpoints…')}</strong>
				</section>
			) : cards.length === 0 ? (
				<section className="empty-state" aria-live="polite">
					<strong>{t('app.ollama.empty', 'No Ollama endpoint is configured yet')}</strong>
					<span className="field-help">
						{t(
							'app.ollama.emptyHint',
							'Add the local daemon or a remote server to start routing local models.',
						)}
					</span>
				</section>
			) : (
				<div className="masonry-grid">
					{cards.map((card) => (
						<EndpointCard
							key={card.id}
							card={card}
							busyAction={busyAction}
							onValidate={() => void runEndpointTask(card.id, 'validate')}
							onSync={() => void runEndpointTask(card.id, 'sync')}
							onSetPreferred={() => setRoleTarget(card)}
						/>
					))}
				</div>
			)}

			<AddEndpointDialog
				open={addOpen}
				token={token}
				onClose={() => setAddOpen(false)}
				onCreated={async () => {
					setAddOpen(false);
					await Promise.all([load(), onRefresh?.()]);
				}}
			/>
			<PreferredRoleDialog
				card={roleTarget}
				rolePolicies={rolePolicies}
				busy={busyAction !== null}
				onClose={() => setRoleTarget(null)}
				onConfirm={(role, model) => void setPreferredForRole(roleTarget?.id ?? '', role, model)}
			/>
		</section>
	);
}

function EndpointCard({
	card,
	busyAction,
	onValidate,
	onSync,
	onSetPreferred,
}: {
	card: EndpointCardModel;
	busyAction: string | null;
	onValidate: () => void;
	onSync: () => void;
	onSetPreferred: () => void;
}) {
	const { t } = useI18n();
	const KindIcon = card.kind === 'local' ? Boxes : Server;
	const validateBusy = busyAction === `${card.id}:validate`;
	const syncBusy = busyAction === `${card.id}:sync`;
	const anyBusy = busyAction !== null;

	return (
		<article className="card card--static" data-tone={card.healthTone}>
			<div className="card-header">
				<div className="inline">
					<KindIcon aria-hidden="true" size={18} />
					<h4 className="card-title">{card.displayName}</h4>
				</div>
				<Badge tone={card.healthTone}>{card.healthStatus}</Badge>
			</div>

			<div className="card-meta">
				<Badge tone="info">
					{card.kind === 'local'
						? t('app.ollama.kind.local', 'local')
						: t('app.ollama.kind.remote', 'remote')}
				</Badge>
				<Badge tone={card.enabled ? 'ok' : 'warn'}>
					{card.enabled ? t('app.ollama.enabled', 'enabled') : t('app.ollama.disabled', 'disabled')}
				</Badge>
				{card.credentialConfigured ? (
					<Badge tone="ok">
						<ShieldCheck aria-hidden="true" size={12} />
						<span>{t('app.ollama.credential', 'bearer token')}</span>
					</Badge>
				) : null}
			</div>

			<dl className="provider-facts">
				<div>
					<dt>{t('app.ollama.card.baseUrl', 'Base URL')}</dt>
					<dd className="mono">{card.baseUrl || t('app.ollama.card.baseUrlUnset', 'not set')}</dd>
				</div>
				<div>
					<dt>{t('app.ollama.card.latency', 'Latency')}</dt>
					<dd className="tnum">
						{card.latencyMs === null
							? t('app.ollama.card.latencyUnknown', 'not measured')
							: `${card.latencyMs} ms`}
					</dd>
				</div>
				<div>
					<dt>{t('app.ollama.card.models', 'Models')}</dt>
					<dd className="tnum">{card.models.length}</dd>
				</div>
			</dl>

			{card.models.length ? (
				<div className="inline">
					{card.models.slice(0, MODEL_CHIP_LIMIT).map((model) => (
						<Badge tone="info" key={model}>
							{model}
						</Badge>
					))}
				</div>
			) : (
				<span className="field-help">
					{t('app.ollama.card.noModels', 'No model synced yet — run Sync models.')}
				</span>
			)}

			{card.preferredRoles.length ? (
				<div className="inline">
					<span className="field-label">
						{t('app.ollama.card.preferredFor', 'Preferred for roles')}
					</span>
					{card.preferredRoles.map((role) => (
						<Badge tone="info" key={role}>
							{role}
						</Badge>
					))}
				</div>
			) : null}

			{card.failureReason ? (
				<p className="card-body">
					<span className="field-label">{t('app.ollama.card.lastError', 'Last error')}</span>{' '}
					{redactVisibleSecret(
						card.failureReason,
						t('app.ollama.card.noReason', 'No reason reported.'),
					)}
				</p>
			) : null}

			<div className="inline">
				<Button
					variant="primary"
					loading={validateBusy}
					disabled={anyBusy}
					icon={<PlugZap size={14} />}
					onClick={onValidate}
				>
					{t('app.ollama.card.validate', 'Validate')}
				</Button>
				<Button
					loading={syncBusy}
					disabled={anyBusy}
					icon={<RefreshCw size={14} />}
					onClick={onSync}
				>
					{t('app.ollama.card.syncModels', 'Sync models')}
				</Button>
				<Button
					icon={<Wand2 size={14} />}
					disabled={anyBusy || card.models.length === 0}
					onClick={onSetPreferred}
				>
					{t('app.ollama.card.setPreferred', 'Set preferred for role')}
				</Button>
			</div>
		</article>
	);
}

/** Registers an endpoint; the backend infers local vs remote from the URL host, never the operator. */
function AddEndpointDialog({
	open,
	token,
	onClose,
	onCreated,
}: {
	open: boolean;
	token: string;
	onClose: () => void;
	onCreated: () => Promise<void>;
}) {
	const { t } = useI18n();
	const [id, setId] = useState('');
	const [displayName, setDisplayName] = useState('');
	const [baseUrl, setBaseUrl] = useState('');
	const [credentialRef, setCredentialRef] = useState('');
	const [enabled, setEnabled] = useState(true);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');

	useEffect(() => {
		if (!open) return;
		setId('');
		setDisplayName('');
		setBaseUrl('');
		setCredentialRef('');
		setEnabled(true);
		setError('');
	}, [open]);

	if (!open) return null;

	const submit = async () => {
		if (busy) return;
		if (!isValidEndpointId(id)) {
			setError(
				t(
					'app.ollama.add.errorId',
					'The id must be lowercase letters, digits, dot, colon, dash or underscore.',
				),
			);
			return;
		}
		if (!isAbsoluteHttpUrl(baseUrl)) {
			setError(
				t('app.ollama.add.errorBaseUrl', 'Enter an absolute http(s) URL with no query string.'),
			);
			return;
		}
		setBusy(true);
		setError('');
		try {
			await createOllamaEndpoint(token, {
				id: id.trim(),
				displayName: displayName.trim() || id.trim(),
				baseUrl: normalizeBaseUrl(baseUrl),
				enabled,
				...(credentialRef.trim() ? { credentialRef: credentialRef.trim() } : {}),
			});
			await onCreated();
		} catch (createError) {
			setError(redactVisibleSecret(errorMessage(createError), 'endpoint creation failed'));
		} finally {
			setBusy(false);
		}
	};

	return (
		<Modal open={open} label={t('app.ollama.add.title', 'Add Ollama endpoint')} onClose={onClose}>
			<div className="form-grid">
				<TextField
					label={t('app.ollama.add.id', 'Endpoint id')}
					value={id}
					autoComplete="off"
					placeholder="ollama-lab"
					onChange={(event) => setId(event.target.value)}
					help={t(
						'app.ollama.add.idHelp',
						'Stable identifier used by routing and the model catalog.',
					)}
				/>
				<TextField
					label={t('app.ollama.add.displayName', 'Display name')}
					value={displayName}
					autoComplete="off"
					onChange={(event) => setDisplayName(event.target.value)}
					help={t('app.ollama.add.displayNameHelp', 'Shown on the card; defaults to the id.')}
				/>
				<TextField
					label={t('app.ollama.add.baseUrl', 'Base URL')}
					value={baseUrl}
					autoComplete="off"
					placeholder="http://192.168.1.20:11434"
					onChange={(event) => setBaseUrl(event.target.value)}
					help={t(
						'app.ollama.add.baseUrlHelp',
						'A loopback host registers a local endpoint; any other host registers a remote one.',
					)}
				/>
				<TextField
					label={t('app.ollama.add.credentialRef', 'Credential reference')}
					value={credentialRef}
					autoComplete="off"
					placeholder="env:ollama_token"
					onChange={(event) => setCredentialRef(event.target.value)}
					help={t(
						'app.ollama.add.credentialRefHelp',
						'Optional bearer token reference for an authenticated remote server; the secret is never shown.',
					)}
				/>
				<Checkbox
					label={t('app.ollama.add.enabled', 'Enable this endpoint')}
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
						{t('app.ollama.add.cancel', 'Cancel')}
					</Button>
					<Button variant="primary" loading={busy} onClick={() => void submit()}>
						{t('app.ollama.add.submit', 'Add endpoint')}
					</Button>
				</div>
			</div>
		</Modal>
	);
}

/** Picks the role whose policy should prefer this endpoint, and with which of its synced models. */
function PreferredRoleDialog({
	card,
	rolePolicies,
	busy,
	onClose,
	onConfirm,
}: {
	card: EndpointCardModel | null;
	rolePolicies: readonly ModelGatewayRolePolicy[];
	busy: boolean;
	onClose: () => void;
	onConfirm: (role: string, model: string) => void;
}) {
	const { t } = useI18n();
	const roles = useMemo(
		() => rolePolicies.map((policy) => policy.role).filter(Boolean),
		[rolePolicies],
	);
	const [role, setRole] = useState('');
	const [model, setModel] = useState('');

	useEffect(() => {
		if (!card) return;
		setRole(roles[0] ?? '');
		setModel(card.models[0] ?? '');
	}, [card, roles]);

	if (!card) return null;

	return (
		<Modal
			open={card !== null}
			label={t('app.ollama.role.title', 'Set preferred for role')}
			onClose={onClose}
		>
			<div className="form-grid">
				<SelectField
					label={t('app.ollama.role.role', 'Role')}
					value={role}
					onChange={(event) => setRole(event.target.value)}
					help={t(
						'app.ollama.role.roleHelp',
						'The endpoint becomes the first preferred candidate of this role.',
					)}
				>
					{roles.map((item) => (
						<option key={item} value={item}>
							{item}
						</option>
					))}
				</SelectField>
				<SelectField
					label={t('app.ollama.role.model', 'Model')}
					value={model}
					onChange={(event) => setModel(event.target.value)}
					help={t('app.ollama.role.modelHelp', 'Only models already synced from this endpoint.')}
				>
					{card.models.map((item) => (
						<option key={item} value={item}>
							{item}
						</option>
					))}
				</SelectField>
				<p className="field-help">
					{t(
						'app.ollama.role.policyNote',
						'Routing still honours the role permissions: a role that blocks local or remote runtimes keeps blocking this endpoint.',
					)}
				</p>
				<div className="wizard-actions">
					<Button onClick={onClose} disabled={busy}>
						{t('app.ollama.role.cancel', 'Cancel')}
					</Button>
					<Button
						variant="primary"
						loading={busy}
						disabled={!role || !model}
						onClick={() => onConfirm(role, model)}
					>
						{t('app.ollama.role.confirm', 'Set as preferred')}
					</Button>
				</div>
			</div>
		</Modal>
	);
}
