/**
 * Endpoint-scoped NVIDIA NIM setup. Each account owns one API contract so chat,
 * retrieval and visual endpoints cannot accidentally share incompatible URLs,
 * credentials or quota policy.
 * @author Rodrigo Mason
 */
import { useEffect, useMemo, useState } from 'react';

import {
	createProviderAccountFromCatalog,
	getNvidiaNimPreflight,
	type ProviderAccountFromCatalogRequest,
} from '../../api/client';
import type { ModelGatewayProviderAccount, NvidiaNimPreflight } from '../../api/types';
import {
	StatusChip as Badge,
	Button,
	Checkbox,
	DataTable,
	EmptyState,
	SelectField,
	TextField,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { boolLabel, SecretSafeValue, text } from './utils';

type ApiFamily = NonNullable<ProviderAccountFromCatalogRequest['apiFamily']>;
type DeploymentMode = NonNullable<ProviderAccountFromCatalogRequest['deploymentMode']>;
type PricingMode = NonNullable<ProviderAccountFromCatalogRequest['pricingMode']>;
type TermsMode = NonNullable<ProviderAccountFromCatalogRequest['termsMode']>;
type Translate = ReturnType<typeof useI18n>['t'];

const HOSTED_BASE_URL = 'https://integrate.api.nvidia.com/v1';

function apiFamilyOptions(t: Translate): Array<{ value: ApiFamily; label: string }> {
	return [
		{
			value: 'chat_completions',
			label: t('app.modelGateway.nim.config.chatCompletions', 'Chat completions'),
		},
		{ value: 'embeddings', label: t('app.modelGateway.nim.config.embeddings', 'Embeddings') },
		{ value: 'rerank', label: t('app.modelGateway.nim.config.rerank', 'Rerank') },
		{
			value: 'image_generation',
			label: t('app.modelGateway.nim.config.imageGeneration', 'Image generation'),
		},
		{
			value: 'image_editing',
			label: t('app.modelGateway.nim.config.imageEditing', 'Image editing'),
		},
	];
}

function deploymentModeOptions(t: Translate): Array<{ value: DeploymentMode; label: string }> {
	return [
		{
			value: 'hosted_trial',
			label: t('app.modelGateway.nim.config.hostedTrial', 'NVIDIA hosted trial'),
		},
		{
			value: 'partner_paid',
			label: t('app.modelGateway.nim.config.partnerPaid', 'Partner / paid endpoint'),
		},
		{
			value: 'self_hosted_development',
			label: t('app.modelGateway.nim.config.selfHostedDevelopment', 'Self-hosted development'),
		},
		{
			value: 'self_hosted_enterprise',
			label: t('app.modelGateway.nim.config.selfHostedEnterprise', 'Self-hosted enterprise'),
		},
	];
}

const PROFILE_MATRIX: Record<ApiFamily, { hosted: string[]; selfHosted: string[] }> = {
	chat_completions: {
		hosted: ['nvidia_openai_chat'],
		selfHosted: ['nvidia_openai_chat'],
	},
	embeddings: {
		hosted: ['nvidia_openai_embeddings'],
		selfHosted: ['nvidia_openai_embeddings'],
	},
	rerank: {
		hosted: ['nvidia_hosted_rerank'],
		selfHosted: ['nvidia_nim_ranking'],
	},
	image_generation: {
		hosted: ['nvidia_hosted_prompt_image_generation'],
		selfHosted: ['nvidia_qwen_image_generation_infer', 'nvidia_openai_image_generation'],
	},
	image_editing: {
		hosted: [],
		selfHosted: ['nvidia_qwen_image_editing_infer', 'nvidia_openai_image_editing'],
	},
};

function isSelfHosted(mode: DeploymentMode): boolean {
	return mode.startsWith('self_hosted');
}

function profilesFor(apiFamily: ApiFamily, deploymentMode: DeploymentMode): string[] {
	return isSelfHosted(deploymentMode)
		? PROFILE_MATRIX[apiFamily].selfHosted
		: PROFILE_MATRIX[apiFamily].hosted;
}

function defaultBaseUrl(apiFamily: ApiFamily, deploymentMode: DeploymentMode): string {
	return deploymentMode === 'hosted_trial' &&
		(apiFamily === 'chat_completions' || apiFamily === 'embeddings')
		? HOSTED_BASE_URL
		: '';
}

function requiresBaseUrl(apiFamily: ApiFamily, deploymentMode: DeploymentMode): boolean {
	return (
		deploymentMode !== 'hosted_trial' ||
		apiFamily === 'rerank' ||
		apiFamily === 'image_generation' ||
		apiFamily === 'image_editing'
	);
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

export function NvidiaNimConfigurationPanel({
	providers,
	token,
	onSaved,
}: {
	providers: ModelGatewayProviderAccount[];
	token: string;
	onSaved: () => void;
}) {
	const { t } = useI18n();
	const apiFamilies = apiFamilyOptions(t);
	const deploymentModes = deploymentModeOptions(t);
	const [instanceId, setInstanceId] = useState('nvidia-nim-chat');
	const [displayName, setDisplayName] = useState('NVIDIA NIM Chat');
	const [deploymentMode, setDeploymentMode] = useState<DeploymentMode>('hosted_trial');
	const [apiFamily, setApiFamily] = useState<ApiFamily>('chat_completions');
	const [adapterProfile, setAdapterProfile] = useState('nvidia_openai_chat');
	const [baseUrl, setBaseUrl] = useState(HOSTED_BASE_URL);
	const [credentialRef, setCredentialRef] = useState('');
	const [termsMode, setTermsMode] = useState<TermsMode>('evaluation');
	const [pricingMode, setPricingMode] = useState<PricingMode>('unknown');
	const [enabled, setEnabled] = useState(true);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');
	const [success, setSuccess] = useState('');
	const [preflight, setPreflight] = useState<NvidiaNimPreflight | null>(null);
	const [preflightBusy, setPreflightBusy] = useState(false);
	const [preflightError, setPreflightError] = useState('');

	useEffect(() => {
		const controller = new AbortController();
		setPreflightBusy(true);
		void getNvidiaNimPreflight(undefined, controller.signal)
			.then((payload) => {
				setPreflight(payload);
				setPreflightError('');
			})
			.catch((loadError) => {
				if (!controller.signal.aborted) setPreflightError(errorMessage(loadError));
			})
			.finally(() => {
				if (!controller.signal.aborted) setPreflightBusy(false);
			});
		return () => controller.abort();
	}, []);

	const nvidiaAccounts = useMemo(
		() => providers.filter((provider) => provider.providerFamily === 'nvidia_nim'),
		[providers],
	);
	const adapterProfiles = profilesFor(apiFamily, deploymentMode);
	const hostedTrial = deploymentMode === 'hosted_trial';
	const credentialRequired = !isSelfHosted(deploymentMode);

	const changeContract = (nextApiFamily: ApiFamily, nextDeploymentMode: DeploymentMode) => {
		const profiles = profilesFor(nextApiFamily, nextDeploymentMode);
		setApiFamily(nextApiFamily);
		setDeploymentMode(nextDeploymentMode);
		setAdapterProfile(profiles[0] ?? '');
		setBaseUrl(defaultBaseUrl(nextApiFamily, nextDeploymentMode));
		if (nextDeploymentMode === 'hosted_trial') {
			setTermsMode('evaluation');
			setPricingMode('unknown');
		} else {
			setTermsMode('unspecified');
			setPricingMode('unknown');
		}
		setError('');
		setSuccess('');
	};

	const refreshPreflight = async () => {
		if (preflightBusy) return;
		setPreflightBusy(true);
		setPreflightError('');
		try {
			setPreflight(await getNvidiaNimPreflight());
		} catch (loadError) {
			setPreflightError(errorMessage(loadError));
		} finally {
			setPreflightBusy(false);
		}
	};

	const save = async () => {
		if (busy) return;
		setError('');
		setSuccess('');
		if (!instanceId.trim()) {
			setError(t('app.nvidiaNim.error.instanceId', 'Enter a stable endpoint id.'));
			return;
		}
		if (!adapterProfile) {
			setError(
				t(
					'app.nvidiaNim.error.unsupportedContract',
					'This API family is not supported by the selected deployment mode.',
				),
			);
			return;
		}
		if (credentialRequired && !credentialRef.trim()) {
			setError(
				t(
					'app.nvidiaNim.error.credentialRef',
					'Hosted and partner endpoints require a configured credential reference.',
				),
			);
			return;
		}
		if (requiresBaseUrl(apiFamily, deploymentMode) && !baseUrl.trim()) {
			setError(
				t(
					'app.nvidiaNim.error.baseUrl',
					'Enter the exact model or self-hosted endpoint URL for this API family.',
				),
			);
			return;
		}
		setBusy(true);
		try {
			await createProviderAccountFromCatalog(token, {
				providerId: 'nvidia_nim',
				instanceId: instanceId.trim(),
				displayName: displayName.trim() || instanceId.trim(),
				deploymentMode,
				apiFamily,
				adapterProfile,
				termsMode: hostedTrial ? 'evaluation' : termsMode,
				pricingMode: hostedTrial ? 'unknown' : pricingMode,
				baseUrl: baseUrl.trim() || null,
				credentialRef: credentialRef.trim() || null,
				enabled,
			});
			setSuccess(
				t(
					'app.nvidiaNim.success.created',
					'Endpoint account created. Configure an explicit model limit before productive use.',
				),
			);
			onSaved();
		} catch (saveError) {
			setError(errorMessage(saveError));
		} finally {
			setBusy(false);
		}
	};

	return (
		<PanelShell title={t('app.nvidiaNim.title', 'NVIDIA NIM endpoints')}>
			<div className="stack">
				<div className="inline">
					<Badge tone="warn">
						{t('app.nvidiaNim.trialBadge', 'Hosted trial: evaluation only')}
					</Badge>
					<a
						className="button"
						href="https://docs.api.nvidia.com/nim/reference/models-1"
						target="_blank"
						rel="noreferrer"
					>
						{t('app.nvidiaNim.modelDocs', 'Official model catalog')}
					</a>
					<a
						className="button"
						href="https://docs.nvidia.com/nim/visual-genai/latest/getting-started.html"
						target="_blank"
						rel="noreferrer"
					>
						{t('app.nvidiaNim.visualDocs', 'Visual NIM docs')}
					</a>
				</div>
				<p className="muted">
					{t(
						'app.nvidiaNim.scopeHelp',
						'Create one account per endpoint contract. Trial access is rate-limited evaluation capacity with no production SLA; local limits remain authoritative when NVIDIA does not publish limit headers.',
					)}
				</p>

				<div className="card card--static">
					<div className="card-header">
						<div className="inline">
							<strong className="card-title">
								{t('app.nvidiaNim.preflight.title', 'Local self-hosted preflight')}
							</strong>
							{preflight ? (
								<Badge tone={preflight.status === 'ready' ? 'ok' : 'warn'}>
									{preflight.status}
								</Badge>
							) : null}
							<Badge tone="info">
								{t('app.nvidiaNim.preflight.readOnly', 'read-only; no mutation')}
							</Badge>
						</div>
						<Button loading={preflightBusy} onClick={() => void refreshPreflight()}>
							{t('app.nvidiaNim.preflight.refresh', 'Refresh preflight')}
						</Button>
					</div>
					{preflightError ? (
						<div className="form-error" role="alert">
							{preflightError}
						</div>
					) : null}
					{preflight ? (
						<div className="stack">
							<div className="grid three">
								<div className="stack compact">
									<strong>{t('app.nvidiaNim.preflight.target', 'Observed target')}</strong>
									<span>
										{preflight.facts.platform.target} / {preflight.facts.platform.architecture}
									</span>
									<span className="muted">
										WSL {text(preflight.facts.platform.wslVersion)} · {preflight.probeScope}
									</span>
								</div>
								<div className="stack compact">
									<strong>{t('app.nvidiaNim.preflight.gpu', 'Detected GPU')}</strong>
									<span>
										{preflight.facts.gpus.length
											? preflight.facts.gpus
													.map((gpu) => `${gpu.name} (${gpu.memoryTotalGiB} GiB)`)
													.join(', ')
											: t('app.nvidiaNim.preflight.noGpu', 'not detected')}
									</span>
									<span className="muted">
										{t('app.nvidiaNim.preflight.memoryScope', 'Memory scope')}:{' '}
										{preflight.facts.resourceScope}
									</span>
								</div>
								<div className="stack compact">
									<strong>{t('app.nvidiaNim.preflight.runtime', 'Container runtime')}</strong>
									<span>
										{preflight.facts.containerRuntimes
											.map(
												(runtime) =>
													`${runtime.id}: ${runtime.serverReachable ? 'reachable' : runtime.installed ? 'client only' : 'missing'}`,
											)
											.join(' · ')}
									</span>
									<span className="muted">
										Toolkit{' '}
										{preflight.facts.containerToolkit.installed ? 'detected' : 'not detected'} · CDI{' '}
										{preflight.facts.containerToolkit.cdiAvailable ? 'detected' : 'not detected'}
									</span>
								</div>
							</div>
							<DataTable
								rows={preflight.profiles}
								empty={
									<EmptyState
										title={t('app.nvidiaNim.preflight.noProfiles', 'No local NIM profiles')}
										body={t(
											'app.nvidiaNim.preflight.noProfilesBody',
											'No sourced local hardware profile is available.',
										)}
									/>
								}
								columns={[
									{
										key: 'profile',
										label: t('app.nvidiaNim.preflight.profile', 'Local profile'),
										render: (row) => (
											<div className="stack compact">
												<strong>{row.displayName}</strong>
												<span className="mono">{row.modelId}</span>
												<a href={row.requirement.sourceUrl} target="_blank" rel="noreferrer">
													{t('app.nvidiaNim.preflight.source', 'Official requirement source')}
												</a>
											</div>
										),
									},
									{
										key: 'status',
										label: t('app.nvidiaNim.preflight.status', 'Compatibility'),
										render: (row) => (
											<div className="stack compact">
												<Badge tone={row.status === 'ready' ? 'ok' : 'warn'}>{row.status}</Badge>
												<span>
													{text(row.observedGpuMemoryGiB)} / {row.requiredGpuMemoryGiB} GiB
												</span>
											</div>
										),
									},
									{
										key: 'blockers',
										label: t('app.nvidiaNim.preflight.blockers', 'Blockers and recovery'),
										render: (row) =>
											row.blockers.length ? (
												<div className="stack compact">
													{row.blockers.map((blocker) => (
														<div className="stack compact" key={blocker.code}>
															<strong className="mono">{blocker.code}</strong>
															<span>{blocker.message}</span>
															<div className="inline">
																{(blocker.actions ?? []).map((action) => (
																	<a
																		className="button"
																		key={action.id}
																		href={
																			action.href ??
																			(action.kind === 'open_configuration'
																				? '#settings-runtime'
																				: '#models')
																		}
																		target={action.href ? '_blank' : undefined}
																		rel={action.href ? 'noreferrer' : undefined}
																	>
																		{action.label}
																	</a>
																))}
															</div>
														</div>
													))}
												</div>
											) : (
												<Badge tone="ok">
													{t('app.nvidiaNim.preflight.noBlockers', 'No blockers')}
												</Badge>
											),
									},
								]}
							/>
						</div>
					) : null}
				</div>

				<DataTable
					rows={nvidiaAccounts}
					empty={
						<EmptyState
							title={t('app.nvidiaNim.empty.title', 'No NVIDIA NIM endpoint accounts')}
							body={t(
								'app.nvidiaNim.empty.body',
								'Add a hosted, partner or self-hosted endpoint below.',
							)}
						/>
					}
					columns={[
						{
							key: 'endpoint',
							label: t('app.nvidiaNim.endpoint', 'Endpoint'),
							render: (row) => (
								<div className="stack compact">
									<strong>{text(row.displayName)}</strong>
									<span className="mono muted">{text(row.providerId)}</span>
								</div>
							),
						},
						{
							key: 'contract',
							label: t('app.nvidiaNim.contract', 'Contract'),
							render: (row) => (
								<div className="stack compact">
									<span>{text(row.apiFamily)}</span>
									<span className="mono muted">{text(row.adapterProfile)}</span>
								</div>
							),
						},
						{
							key: 'mode',
							label: t('app.nvidiaNim.mode', 'Deployment'),
							render: (row) => text(row.deploymentMode),
						},
						{
							key: 'baseUrl',
							label: t('app.nvidiaNim.baseUrl', 'Base URL'),
							render: (row) => <SecretSafeValue value={row.baseUrl} />,
						},
						{
							key: 'credential',
							label: t('app.nvidiaNim.credential', 'Credential'),
							render: (row) => (
								<div className="stack compact">
									<Badge tone={row.credentialStatus === 'configured' ? 'ok' : 'warn'}>
										{text(row.credentialStatus)}
									</Badge>
									<SecretSafeValue value={row.credentialRef} />
								</div>
							),
						},
						{
							key: 'terms',
							label: t('app.nvidiaNim.termsPricing', 'Terms / pricing'),
							render: (row) => `${text(row.termsMode)} / ${text(row.pricingMode)}`,
						},
						{
							key: 'enabled',
							label: t('ui.static.enabled.df174a3f', 'Enabled'),
							render: (row) => (
								<Badge tone={row.enabled ? 'ok' : 'warn'}>{boolLabel(row.enabled)}</Badge>
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
					<div className="grid two">
						<TextField
							label={t('app.nvidiaNim.form.instanceId', 'Endpoint id')}
							value={instanceId}
							onChange={(event) => setInstanceId(event.target.value)}
							autoComplete="off"
							required
							help={t(
								'app.nvidiaNim.form.instanceIdHelp',
								'Stable id used by routing, limits and execution branches.',
							)}
						/>
						<TextField
							label={t('app.nvidiaNim.form.displayName', 'Display name')}
							value={displayName}
							onChange={(event) => setDisplayName(event.target.value)}
							autoComplete="off"
						/>
						<SelectField
							label={t('app.nvidiaNim.form.deploymentMode', 'Deployment mode')}
							value={deploymentMode}
							onChange={(event) => changeContract(apiFamily, event.target.value as DeploymentMode)}
						>
							{deploymentModes.map((mode) => (
								<option key={mode.value} value={mode.value}>
									{mode.label}
								</option>
							))}
						</SelectField>
						<SelectField
							label={t('app.nvidiaNim.form.apiFamily', 'API family')}
							value={apiFamily}
							onChange={(event) => changeContract(event.target.value as ApiFamily, deploymentMode)}
						>
							{apiFamilies.map((family) => (
								<option key={family.value} value={family.value}>
									{family.label}
								</option>
							))}
						</SelectField>
						<SelectField
							label={t('app.nvidiaNim.form.adapterProfile', 'Adapter profile')}
							value={adapterProfile}
							onChange={(event) => setAdapterProfile(event.target.value)}
							disabled={adapterProfiles.length === 0}
							error={
								adapterProfiles.length === 0
									? t(
											'app.nvidiaNim.form.unsupported',
											'Hosted arbitrary image editing is not supported by this contract.',
										)
									: undefined
							}
						>
							{adapterProfiles.map((profile) => (
								<option key={profile} value={profile}>
									{profile}
								</option>
							))}
						</SelectField>
						<TextField
							label={t('app.nvidiaNim.form.baseUrl', 'Exact base URL')}
							value={baseUrl}
							onChange={(event) => setBaseUrl(event.target.value)}
							autoComplete="url"
							required={requiresBaseUrl(apiFamily, deploymentMode)}
							help={t(
								'app.nvidiaNim.form.baseUrlHelp',
								'Rerank and hosted visual endpoints use model-specific roots; self-hosted endpoints use the deployed NIM root.',
							)}
						/>
						<TextField
							label={t('app.nvidiaNim.form.credentialRef', 'Credential reference')}
							value={credentialRef}
							onChange={(event) => setCredentialRef(event.target.value)}
							autoComplete="off"
							required={credentialRequired}
							help={t(
								'app.nvidiaNim.form.credentialRefHelp',
								'Vault or environment reference only. Never paste the API key into this form.',
							)}
						/>
						<SelectField
							label={t('app.nvidiaNim.form.terms', 'Terms state')}
							value={hostedTrial ? 'evaluation' : termsMode}
							disabled={hostedTrial}
							onChange={(event) => setTermsMode(event.target.value as TermsMode)}
						>
							<option value="unspecified">unspecified</option>
							<option value="accepted">accepted</option>
							{hostedTrial ? <option value="evaluation">evaluation</option> : null}
						</SelectField>
						<SelectField
							label={t('app.nvidiaNim.form.pricing', 'Pricing state')}
							value={hostedTrial ? 'unknown' : pricingMode}
							disabled={hostedTrial}
							onChange={(event) => setPricingMode(event.target.value as PricingMode)}
							help={t(
								'app.nvidiaNim.form.pricingHelp',
								'Use configured only after endpoint-scoped prices exist in the pricing catalog.',
							)}
						>
							<option value="unknown">unknown</option>
							<option value="configured">configured</option>
						</SelectField>
					</div>
					<Checkbox
						label={t('app.nvidiaNim.form.enabled', 'Enable endpoint after creation')}
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
						<Button
							variant="primary"
							type="submit"
							loading={busy}
							disabled={adapterProfiles.length === 0}
						>
							{t('app.nvidiaNim.form.submit', 'Create endpoint account')}
						</Button>
					</div>
				</form>
			</div>
		</PanelShell>
	);
}
