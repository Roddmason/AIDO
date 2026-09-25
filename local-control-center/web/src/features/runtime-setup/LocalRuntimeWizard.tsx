/**
 * Setup wizard for local OpenAI-compatible servers (llama.cpp, LM Studio, vLLM, generic): Endpoint →
 * Models → Validate → Done, written through `/api/v1/local-endpoints`. The endpoint step keeps the
 * base URL editable and reads back the stored one when the account already exists (a re-save never
 * resets it), names the instance, takes an optional credential reference and warns when the host is
 * not loopback, offering the audited "runs on this machine (WSL/Docker)" declaration. The models step
 * shows each model's load state and edits enabled, default and code capabilities; the validate step
 * runs the real validation of the default model. Secrets never enter this component: only references.
 * @author Rodrigo Mason
 */

import { CheckCircle2, KeyRound, RefreshCw, XCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
	createLocalEndpoint,
	declareLocalEndpoint,
	getLocalEndpoints,
	healthCheckModelGatewayProvider,
	type LocalEndpointView,
	type LocalModelPatchRequest,
	patchLocalEndpoint,
	patchLocalEndpointModel,
	type RuntimeValidationResponse,
	syncProviderAccountModels,
	validateLocalEndpointModel,
} from '../../api/client';
import {
	StatusChip as Badge,
	Button,
	Checkbox,
	SelectField,
	type StatusTone,
	TextField,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import {
	defaultModelOf,
	errorCode,
	errorMessage,
	isLoopbackUrl,
	LOAD_STATE_META,
	LOCALITY_META,
	type LocalRuntimeDraft,
} from './localEndpoints';
import { isAbsoluteHttpUrl, isValidEndpointId, normalizeBaseUrl } from './ollamaEndpoints';
import { describeReason } from './reasonCopy';
import { catalogEntry } from './runtimeSetup';

type LocalWizardStep = 'endpoint' | 'models' | 'validate' | 'done';
const LOCAL_STEP_ORDER: LocalWizardStep[] = ['endpoint', 'models', 'validate', 'done'];
/** Health results that let the wizard continue: a model that is still loading is not a failure. */
const READY_HEALTH = new Set(['healthy', 'model_loading']);

type ValidationRecord = RuntimeValidationResponse['validation'];
type ModelPatch = Omit<LocalModelPatchRequest, 'model'>;

const VALIDATION_META: Record<
	ValidationRecord['status'],
	{ tone: StatusTone; labelKey: string; fallback: string }
> = {
	validated: {
		tone: 'ok',
		labelKey: 'app.localRuntime.validation.validated',
		fallback: 'Validated',
	},
	deferred: {
		tone: 'warn',
		labelKey: 'app.localRuntime.validation.deferred',
		fallback: 'Deferred',
	},
	failed: { tone: 'danger', labelKey: 'app.localRuntime.validation.failed', fallback: 'Failed' },
};

async function readEndpoint(endpointId: string): Promise<LocalEndpointView> {
	const payload = await getLocalEndpoints();
	const endpoint = payload.endpoints.find((item) => item.id === endpointId);
	if (!endpoint) throw new Error(`local endpoint not found: ${endpointId}`);
	return endpoint;
}

export type LocalRuntimeWizardProps = {
	/** What to set up; null keeps the wizard closed. */
	draft: LocalRuntimeDraft | null;
	token: string;
	onClose: () => void;
	/** Called once the wizard wrote anything, so the panels re-read the control plane. */
	onSaved: () => void;
};

export function LocalRuntimeWizard({ draft, token, onClose, onSaved }: LocalRuntimeWizardProps) {
	const { t } = useI18n();
	const [step, setStep] = useState<LocalWizardStep>('endpoint');
	const [baseUrl, setBaseUrl] = useState('');
	const [instanceId, setInstanceId] = useState('');
	const [displayName, setDisplayName] = useState('');
	const [credentialRef, setCredentialRef] = useState('');
	const [declareLocal, setDeclareLocal] = useState(false);
	/** Stored endpoint this wizard writes to; null until the first save creates it. */
	const [endpointId, setEndpointId] = useState<string | null>(null);
	const [endpoint, setEndpoint] = useState<LocalEndpointView | null>(null);
	const [validation, setValidation] = useState<ValidationRecord | null>(null);
	const [dirty, setDirty] = useState(false);
	const [prefillFailed, setPrefillFailed] = useState(false);
	const [busy, setBusy] = useState<string | null>(null);
	const [error, setError] = useState('');
	const titleRef = useRef<HTMLHeadingElement>(null);

	// biome-ignore lint/correctness/useExhaustiveDependencies: each wizard step must move keyboard focus to its heading.
	useEffect(() => {
		if (draft) titleRef.current?.focus();
	}, [draft, step]);

	useEffect(() => {
		if (!draft) return;
		setStep('endpoint');
		setBaseUrl(draft.baseUrl);
		setInstanceId('');
		setDisplayName(draft.displayName);
		setCredentialRef('');
		setDeclareLocal(false);
		setEndpointId(draft.endpointId);
		setEndpoint(null);
		setValidation(null);
		setDirty(false);
		setPrefillFailed(false);
		setBusy(null);
		setError('');
		if (draft.source !== 'catalog') return;
		const controller = new AbortController();
		getLocalEndpoints(controller.signal)
			.then((payload) => {
				const stored = payload.endpoints.find((item) => item.id === draft.catalogId);
				if (!stored) return;
				setEndpointId(stored.id);
				setBaseUrl(stored.baseUrl);
				setDisplayName(stored.displayName);
			})
			.catch(() => {
				if (!controller.signal.aborted) setPrefillFailed(true);
			});
		return () => controller.abort();
	}, [draft]);

	if (!draft) return null;

	const stepIndex = LOCAL_STEP_ORDER.indexOf(step);
	const stepLabels: Record<LocalWizardStep, string> = {
		endpoint: t('app.localRuntime.wizard.stepEndpoint', 'Endpoint'),
		models: t('app.localRuntime.wizard.stepModels', 'Models'),
		validate: t('app.localRuntime.wizard.stepValidate', 'Validate'),
		done: t('app.localRuntime.wizard.stepDone', 'Done'),
	};
	const serverName = catalogEntry(draft.catalogId)?.displayName ?? draft.catalogId;
	const remoteHost = isAbsoluteHttpUrl(baseUrl) && !isLoopbackUrl(baseUrl);
	const models = endpoint?.models ?? [];
	const enabledModels = models.filter((model) => model.enabled).map((model) => model.model);
	const defaultModel = endpoint ? defaultModelOf(endpoint) : null;
	const locality = endpoint ? LOCALITY_META[endpoint.locality] : null;
	const validationMeta = validation ? VALIDATION_META[validation.status] : null;

	const saveEndpoint = async () => {
		if (busy) return;
		if (!isAbsoluteHttpUrl(baseUrl)) {
			setError(
				t(
					'app.localRuntime.wizard.errorBaseUrl',
					'Enter an absolute http(s) URL with no query string.',
				),
			);
			return;
		}
		const instance = instanceId.trim();
		if (!endpointId && instance && !isValidEndpointId(instance)) {
			setError(
				t(
					'app.localRuntime.wizard.errorInstanceId',
					'Use 2 to 96 lowercase letters, digits, dot, colon, dash or underscore for the instance name, starting with a letter or digit.',
				),
			);
			return;
		}
		setBusy('endpoint');
		setError('');
		try {
			const url = normalizeBaseUrl(baseUrl);
			const name = displayName.trim();
			const ref = credentialRef.trim();
			const saved = endpointId
				? await patchLocalEndpoint(token, endpointId, {
						baseUrl: url,
						...(name ? { displayName: name } : {}),
						...(ref ? { credentialRef: ref } : {}),
					})
				: await createLocalEndpoint(token, {
						catalogId: draft.catalogId,
						baseUrl: url,
						...(instance ? { instanceId: instance } : {}),
						...(name ? { displayName: name } : {}),
						...(ref ? { credentialRef: ref } : {}),
					});
			setEndpointId(saved.id);
			setDirty(true);
			if (declareLocal && remoteHost && !saved.declaredLocal) {
				await declareLocalEndpoint(token, saved.id, true);
			}
			const { health } = await healthCheckModelGatewayProvider(token, saved.id);
			if (!READY_HEALTH.has(health.healthStatus)) {
				setError(
					`${t('app.localRuntime.wizard.notReady', 'The server did not pass the health check:')} ${describeReason(
						redactVisibleSecret(health.lastError || health.message, health.healthStatus),
						t,
					)}`,
				);
				return;
			}
			await syncProviderAccountModels(token, saved.id);
			setEndpoint(await readEndpoint(saved.id));
			setStep('models');
		} catch (saveError) {
			setError(
				errorCode(saveError) === 'local_declaration_host_not_allowed'
					? t(
							'app.localRuntime.wizard.declarationRejected',
							'Only a private or link-local IP address, or host.docker.internal, can be declared as running on this machine.',
						)
					: redactVisibleSecret(errorMessage(saveError), 'local endpoint save failed'),
			);
		} finally {
			setBusy(null);
		}
	};

	const refreshModels = async (task: () => Promise<unknown>, busyKey: string) => {
		if (!endpoint || busy) return;
		setBusy(busyKey);
		setError('');
		try {
			await task();
			setEndpoint(await readEndpoint(endpoint.id));
		} catch (taskError) {
			setError(redactVisibleSecret(errorMessage(taskError), 'local model update failed'));
		} finally {
			setBusy(null);
		}
	};

	const updateModel = (model: string, patch: ModelPatch) => {
		if (!endpoint) return;
		void refreshModels(
			() => patchLocalEndpointModel(token, endpoint.id, { model, ...patch }),
			`model:${model}`,
		);
	};

	const resyncModels = () => {
		if (!endpoint) return;
		void refreshModels(() => syncProviderAccountModels(token, endpoint.id), 'sync');
	};

	const runValidation = async () => {
		if (!endpoint || !defaultModel || busy) return;
		setBusy('validate');
		setError('');
		setValidation(null);
		try {
			const response = await validateLocalEndpointModel(token, endpoint.id, defaultModel);
			setValidation(response.validation);
			setEndpoint(await readEndpoint(endpoint.id));
		} catch (validateError) {
			setError(redactVisibleSecret(errorMessage(validateError), 'local model validation failed'));
		} finally {
			setBusy(null);
		}
	};

	const close = () => {
		if (dirty) onSaved();
		onClose();
	};

	const goNext = () => {
		if (step === 'endpoint') {
			void saveEndpoint();
			return;
		}
		if (step === 'models') {
			if (!defaultModel) {
				setError(
					t(
						'app.localRuntime.wizard.errorDefaultModel',
						'Enable a model and make it the default before validating.',
					),
				);
				return;
			}
			setError('');
			setStep('validate');
			return;
		}
		if (step === 'validate') {
			setError('');
			setStep('done');
		}
	};

	const goBack = () => {
		setError('');
		if (stepIndex === 0) {
			close();
			return;
		}
		setStep(LOCAL_STEP_ORDER[stepIndex - 1]);
	};

	return (
		<section
			className="provider-setup-wizard"
			aria-label={t('app.localRuntime.wizard.title', 'Set up a local runtime')}
		>
			<div className="surface-toolbar">
				<h3 ref={titleRef} tabIndex={-1}>
					{t('app.localRuntime.wizard.title', 'Set up a local runtime')} · {serverName}
				</h3>
				<Button disabled={busy !== null} onClick={close}>
					{t('app.localRuntime.wizard.close', 'Close local runtime setup')}
				</Button>
			</div>
			<div className="form-grid">
				<ol
					className="wizard-steps"
					aria-label={t('app.localRuntime.wizard.stepsAria', 'Local runtime setup steps')}
				>
					{LOCAL_STEP_ORDER.map((item, index) => (
						<li
							key={item}
							className={item === step ? 'current' : ''}
							aria-current={item === step ? 'step' : undefined}
						>
							<span className="mono">0{index + 1}</span>
							<strong>{stepLabels[item]}</strong>
						</li>
					))}
				</ol>

				{step === 'endpoint' ? (
					<>
						<TextField
							label={t('app.localRuntime.wizard.baseUrl', 'Base URL')}
							value={baseUrl}
							autoComplete="off"
							placeholder="http://127.0.0.1:8082/v1"
							onChange={(event) => setBaseUrl(event.target.value)}
							help={t(
								'app.localRuntime.wizard.baseUrlHelp',
								'Prefilled with the server default; change it if your server listens elsewhere. Saving again keeps what you enter here.',
							)}
						/>
						{endpointId ? (
							<div className="field">
								<span className="field-label">
									{t('app.localRuntime.wizard.instanceId', 'Instance name')}
								</span>
								<span className="mono">{endpointId}</span>
							</div>
						) : (
							<TextField
								label={t('app.localRuntime.wizard.instanceId', 'Instance name')}
								value={instanceId}
								autoComplete="off"
								placeholder={draft.catalogId}
								onChange={(event) => setInstanceId(event.target.value)}
								help={t(
									'app.localRuntime.wizard.instanceIdHelp',
									'Optional. Name a second server of the same kind, for example llama-lab; empty uses the catalog id.',
								)}
							/>
						)}
						<TextField
							label={t('app.localRuntime.wizard.displayName', 'Display name')}
							value={displayName}
							autoComplete="off"
							onChange={(event) => setDisplayName(event.target.value)}
						/>
						<TextField
							label={t('app.localRuntime.wizard.credentialRef', 'Token reference (optional)')}
							value={credentialRef}
							autoComplete="off"
							placeholder="env:llama_api_key"
							onChange={(event) => setCredentialRef(event.target.value)}
							help={t(
								'app.localRuntime.wizard.credentialRefHelp',
								'Only when the server was started with an API key. Reference an existing secret (env:, keyring:, openbao:, vault:); the secret is never shown.',
							)}
						/>
						{remoteHost ? (
							<section
								className="stack compact"
								aria-label={t('app.localRuntime.wizard.remoteHostAria', 'Host is not loopback')}
							>
								<Badge tone="warn">
									{t('app.localRuntime.wizard.remoteHost', 'Not a loopback host')}
								</Badge>
								<p className="field-help">
									{t(
										'app.localRuntime.wizard.remoteHostHelp',
										'AIDO treats this server as remote: local-only privacy rejects it and a token is never sent to it over plain http. If it runs on this machine through WSL or Docker, declare it here.',
									)}
								</p>
								<Checkbox
									label={t(
										'app.localRuntime.wizard.declareLocal',
										'Runs on this machine (WSL/Docker)',
									)}
									help={t(
										'app.localRuntime.wizard.declareLocalHelp',
										'Recorded in the audit log. Accepted only for private or link-local IP addresses and host.docker.internal.',
									)}
									checked={declareLocal}
									onChange={(event) => setDeclareLocal(event.target.checked)}
								/>
							</section>
						) : null}
						{prefillFailed ? (
							<p className="field-help" role="status">
								{t(
									'app.localRuntime.wizard.prefillFailed',
									'Could not read the stored endpoint; check the URL before saving.',
								)}
							</p>
						) : null}
					</>
				) : null}

				{step === 'models' ? (
					<>
						<div className="surface-toolbar">
							<div className="inline">
								<span className="muted">
									<span className="tnum">{enabledModels.length}</span>/
									<span className="tnum">{models.length}</span>{' '}
									{t('app.localRuntime.wizard.modelsEnabled', 'enabled')}
								</span>
								{locality ? (
									<Badge tone={locality.tone}>{t(locality.labelKey, locality.fallback)}</Badge>
								) : null}
							</div>
							<Button
								icon={<RefreshCw size={15} />}
								loading={busy === 'sync'}
								disabled={busy !== null}
								onClick={resyncModels}
							>
								{t('app.localRuntime.wizard.resync', 'Sync models again')}
							</Button>
						</div>
						{models.length === 0 ? (
							<p className="field-help" role="status">
								{t(
									'app.localRuntime.wizard.noModels',
									'The server listed no models. Load or download one, then sync again.',
								)}
							</p>
						) : null}
						<div className="stack compact provider-model-list">
							{models.map((model) => {
								const state = LOAD_STATE_META[model.loadState] ?? LOAD_STATE_META.unknown;
								return (
									<div
										className="inline local-model-row"
										key={model.model}
										data-model={model.model}
									>
										<strong className="mono">{model.model}</strong>
										<Badge tone={state.tone}>{t(state.labelKey, state.fallback)}</Badge>
										{model.isDefault ? (
											<Badge tone="ok">{t('app.localRuntime.wizard.isDefault', 'default')}</Badge>
										) : null}
										<Checkbox
											label={t('app.localRuntime.wizard.modelEnabled', 'Enabled')}
											checked={model.enabled}
											disabled={busy !== null}
											onChange={(event) =>
												updateModel(model.model, { enabled: event.target.checked })
											}
										/>
										<Checkbox
											label={t('app.localRuntime.wizard.codeEdit', 'Can edit code')}
											checked={model.codeEdit}
											disabled={busy !== null}
											onChange={(event) =>
												updateModel(model.model, { codeEdit: event.target.checked })
											}
										/>
										<Checkbox
											label={t('app.localRuntime.wizard.codeReview', 'Can review code')}
											checked={model.codeReview}
											disabled={busy !== null}
											onChange={(event) =>
												updateModel(model.model, { codeReview: event.target.checked })
											}
										/>
									</div>
								);
							})}
						</div>
						<SelectField
							label={t('app.localRuntime.wizard.defaultModel', 'Default model')}
							value={defaultModel ?? ''}
							disabled={busy !== null || enabledModels.length === 0}
							onChange={(event) => {
								const value = event.target.value;
								if (value) updateModel(value, { isDefault: true });
							}}
							help={t(
								'app.localRuntime.wizard.defaultModelHelp',
								'Used when no loaded model fits a role. Code capabilities are opt-in per model.',
							)}
						>
							<option value="">
								{t('app.localRuntime.wizard.chooseDefault', 'Choose a default model')}
							</option>
							{enabledModels.map((model) => (
								<option key={model} value={model}>
									{model}
								</option>
							))}
						</SelectField>
					</>
				) : null}

				{step === 'validate' ? (
					<>
						<p className="field-help">
							{t(
								'app.localRuntime.wizard.validateHelp',
								'Runs a real chat round trip with the default model and checks that it returns valid JSON. A model that is still loading is not a failure: try again when it is ready.',
							)}
						</p>
						<div className="field">
							<span className="field-label">
								{t('app.localRuntime.wizard.defaultModel', 'Default model')}
							</span>
							<span className="mono">{defaultModel}</span>
						</div>
						<Button
							variant="primary"
							loading={busy === 'validate'}
							disabled={busy !== null || !defaultModel}
							icon={<CheckCircle2 size={15} />}
							onClick={() => void runValidation()}
						>
							{t('app.localRuntime.wizard.validate', 'Validate default model')}
						</Button>
						{validation && validationMeta ? (
							<div className="inline" role="status">
								{validation.status === 'validated' ? (
									<CheckCircle2 aria-hidden="true" size={15} />
								) : (
									<XCircle aria-hidden="true" size={15} />
								)}
								<Badge tone={validationMeta.tone}>
									{t(validationMeta.labelKey, validationMeta.fallback)}
								</Badge>
								{typeof validation.latencyMs === 'number' ? (
									<span className="mono">{validation.latencyMs} ms</span>
								) : null}
								{validation.reason ? (
									<span className="field-help">
										{describeReason(redactVisibleSecret(validation.reason), t)}
									</span>
								) : null}
							</div>
						) : null}
					</>
				) : null}

				{step === 'done' ? (
					<>
						<dl className="provider-facts">
							<div>
								<dt>{t('app.localRuntime.wizard.summaryEndpoint', 'Endpoint')}</dt>
								<dd className="mono">{endpoint?.id}</dd>
							</div>
							<div>
								<dt>{t('app.localRuntime.wizard.baseUrl', 'Base URL')}</dt>
								<dd className="mono">{endpoint?.baseUrl}</dd>
							</div>
							<div>
								<dt>{t('app.localRuntime.wizard.defaultModel', 'Default model')}</dt>
								<dd className="mono">{defaultModel}</dd>
							</div>
							<div>
								<dt>{t('app.localRuntime.wizard.summaryValidation', 'Validation')}</dt>
								<dd>
									{validationMeta
										? t(validationMeta.labelKey, validationMeta.fallback)
										: t('app.localRuntime.wizard.notValidated', 'not validated')}
								</dd>
							</div>
						</dl>
						<p className="field-help">
							{t(
								'app.localRuntime.wizard.doneHelp',
								'The runtime is listed under Local endpoints and, once validated, offered as a candidate for thread teams.',
							)}
						</p>
					</>
				) : null}

				{error ? (
					<div className="form-error" role="alert">
						{error}
					</div>
				) : null}

				<div className="wizard-actions">
					<Button onClick={goBack} disabled={busy !== null}>
						{stepIndex === 0
							? t('app.localRuntime.wizard.cancel', 'Cancel')
							: t('app.localRuntime.wizard.back', 'Back')}
					</Button>
					{step === 'done' ? (
						<Button
							variant="primary"
							icon={<KeyRound size={15} />}
							onClick={() => {
								onSaved();
								onClose();
							}}
						>
							{t('app.localRuntime.wizard.finish', 'Finish')}
						</Button>
					) : (
						<Button
							variant="primary"
							loading={busy === 'endpoint'}
							disabled={
								(busy !== null && busy !== 'endpoint') ||
								(step === 'validate' && validation === null)
							}
							onClick={goNext}
						>
							{t('app.localRuntime.wizard.next', 'Next')}
						</Button>
					)}
				</div>
			</div>
		</section>
	);
}
