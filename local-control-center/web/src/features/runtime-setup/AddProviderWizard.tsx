/**
 * "Add provider" wizard for the Providers & CLI setup catalog. A five-step modal — choose provider,
 * enter a required or optional credential reference (base URL only for custom/remote/Azure), sync
 * and pick models, validate, then assign roles — that writes through the real control plane: it
 * creates a vault credential (secret in, never out), enables the provider account, discovers its
 * models, runs a real test prompt and routes the chosen model to the selected roles. Auto-routing
 * gateways (OmniRoute) sync their catalog on entering the models step and route roles to the
 * provider-level wildcard — the same `{provider, model: "*"}` candidate scripts/setup_omniroute.py
 * pins — because the gateway, not the role, picks the concrete model per request. The API key is
 * held in an uncontrolled masked input — never in React state, never serialized into the DOM — read
 * once to create the credential, and cleared the moment the provider is saved.
 * @author Rodrigo Mason
 */

import { CheckCircle2, CircleDollarSign, KeyRound, Link2, RefreshCw, XCircle } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
	createCredential,
	createProviderAccountFromCatalog,
	getCredentials,
	getModelGatewayRolePolicies,
	healthCheckModelGatewayProvider,
	patchModelGatewayModel,
	patchModelGatewayRolePolicy,
	syncProviderAccountModels,
	testPromptModelGatewayProvider,
} from '../../api/client';
import type { CredentialBackend, ModelGatewayModel, ModelGatewayRolePolicy } from '../../api/types';
import {
	StatusChip as Badge,
	Button,
	Checkbox,
	Dialog as Modal,
	SegmentedControl,
	SelectField,
	TextField,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import {
	COST_META,
	costForModels,
	nextPreferredCandidates,
	rolePreferredNeedsUpdate,
	rolePrefersProvider,
} from './providerCardModel';
import { catalogEntry, PROVIDER_CATALOG, type ProviderCatalogEntry } from './runtimeSetup';

type WizardStep = 'provider' | 'credential' | 'models' | 'validate' | 'roles';
const STEP_ORDER: WizardStep[] = ['provider', 'credential', 'models', 'validate', 'roles'];

type TestOutcome = { ok: boolean; latencyMs?: number; sample?: string; error?: string | null };
type CredentialMode = 'none' | 'key' | 'ref' | 'keep';
type GeminiPricingMode = 'free' | 'configured';
const GEMINI_MODEL_PRIORITY = [
	'gemini-3.5-flash',
	'gemini-3.1-flash-lite',
	'gemini-2.5-flash',
	'gemini-2.5-flash-lite',
	'gemini-2.5-pro',
] as const;

/** Gateways that pick the concrete model per request; roles route to their wildcard candidate. */
const AUTO_ROUTING_GATEWAYS = new Set(['omniroute']);
const AUTO_ROUTING_MODEL = '*';

export function orderedRoleModels(providerId: string, models: string[]): string[] {
	if (providerId !== 'gemini') return models;
	const priority = new Map<string, number>(
		GEMINI_MODEL_PRIORITY.map((model, index) => [model, index]),
	);
	return [...models].sort(
		(left, right) =>
			(priority.get(left) ?? GEMINI_MODEL_PRIORITY.length) -
			(priority.get(right) ?? GEMINI_MODEL_PRIORITY.length),
	);
}

/** Non-CLI providers the wizard can connect (CLI runtimes are set up via Detect & check on the card). */
const WIZARD_PROVIDERS = PROVIDER_CATALOG.filter((entry) => entry.group !== 'cli');

/** What the provider asks the operator for, stated before any field is shown. */
const AUTH_META: Record<
	ProviderCatalogEntry['authKind'],
	{ tone: 'warn' | 'info'; labelKey: string; fallback: string }
> = {
	api_key: {
		tone: 'warn',
		labelKey: 'app.providers.wizard.authRequired',
		fallback: 'API key required',
	},
	optional_api_key: {
		tone: 'info',
		labelKey: 'app.providers.wizard.authOptional',
		fallback: 'Token optional',
	},
	none: {
		tone: 'info',
		labelKey: 'app.providers.wizard.authNone',
		fallback: 'No credential needed',
	},
};

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

/**
 * Reopening the wizard on a configured account must not demand the secret again: the vault never
 * returns it, so 'keep' reuses the stored reference and only the fields the operator came to change
 * are touched.
 */
function defaultCredentialMode(
	entry: ProviderCatalogEntry | undefined,
	storedCredentialRef: string,
): CredentialMode {
	if (storedCredentialRef.trim()) return 'keep';
	return entry?.authKind === 'optional_api_key' ? 'none' : 'key';
}

export function AddProviderWizard({
	open,
	token,
	initialProviderId,
	initialPricingMode,
	initialFreeTierAttested = false,
	initialCredentialRef = '',
	initialBaseUrl = '',
	onClose,
	onSaved,
}: {
	open: boolean;
	token: string;
	initialProviderId?: string | null;
	initialPricingMode?: 'unknown' | GeminiPricingMode | null;
	initialFreeTierAttested?: boolean;
	initialCredentialRef?: string;
	initialBaseUrl?: string;
	onClose: () => void;
	onSaved: () => void;
}) {
	const { t } = useI18n();
	const [step, setStep] = useState<WizardStep>('provider');
	const [providerId, setProviderId] = useState<string>(
		initialProviderId ?? WIZARD_PROVIDERS[0]?.id ?? '',
	);
	const [credMode, setCredMode] = useState<CredentialMode>('key');
	const [geminiPricingMode, setGeminiPricingMode] = useState<GeminiPricingMode>(
		initialPricingMode === 'configured' ? 'configured' : 'free',
	);
	const [geminiFreeTierAttested, setGeminiFreeTierAttested] = useState(initialFreeTierAttested);
	const [credentialRef, setCredentialRef] = useState('');
	const [baseUrl, setBaseUrl] = useState('');
	const [backends, setBackends] = useState<CredentialBackend[]>([]);
	const [discovered, setDiscovered] = useState<ModelGatewayModel[]>([]);
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [validation, setValidation] = useState<TestOutcome | null>(null);
	const [rolePolicies, setRolePolicies] = useState<ModelGatewayRolePolicy[]>([]);
	const [assignedRoles, setAssignedRoles] = useState<Set<string>>(new Set());
	const [roleModel, setRoleModel] = useState('');
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');

	/**
	 * The API key stays out of React state: a controlled input mirrors its value into the `value`
	 * HTML attribute, so the plaintext secret would show up in any DOM serialization. Uncontrolled,
	 * it lives only in the masked input and is read once, when the credential is created.
	 */
	const apiKeyRef = useRef<HTMLInputElement | null>(null);
	const readApiKey = () => apiKeyRef.current?.value ?? '';
	const clearApiKey = useCallback(() => {
		if (apiKeyRef.current) apiKeyRef.current.value = '';
	}, []);

	const entry: ProviderCatalogEntry | undefined = catalogEntry(providerId);

	useEffect(() => {
		if (!open) return;
		const first = initialProviderId ?? WIZARD_PROVIDERS[0]?.id ?? '';
		const seedEntry = catalogEntry(first);
		setStep('provider');
		setProviderId(first);
		setCredMode(defaultCredentialMode(seedEntry, initialCredentialRef));
		setGeminiPricingMode(initialPricingMode === 'configured' ? 'configured' : 'free');
		setGeminiFreeTierAttested(initialFreeTierAttested);
		clearApiKey();
		setCredentialRef(initialCredentialRef);
		setBaseUrl(initialBaseUrl || (seedEntry?.defaultBaseUrl ?? ''));
		setDiscovered([]);
		setSelected(new Set());
		setValidation(null);
		setError('');
		void getCredentials()
			.then((payload) => setBackends(payload.backends))
			.catch(() => setBackends([]));
		void getModelGatewayRolePolicies()
			.then((payload) => setRolePolicies(payload.rolePolicies))
			.catch(() => setRolePolicies([]));
	}, [
		open,
		initialProviderId,
		initialPricingMode,
		initialFreeTierAttested,
		initialCredentialRef,
		initialBaseUrl,
		clearApiKey,
	]);

	const writableBackend = useMemo(
		() =>
			backends.find((backend) => backend.configured && !backend.readOnly && backend.default)
				?.kind ??
			backends.find((backend) => backend.configured && !backend.readOnly)?.kind ??
			'',
		[backends],
	);

	/** Model ids the operator kept — the only ones a role may be routed to. */
	const selectedModels = useMemo(
		() =>
			orderedRoleModels(
				providerId,
				discovered.filter((model) => selected.has(model.id)).map((model) => model.model),
			),
		[discovered, selected, providerId],
	);

	/** An auto-routing gateway leads with the wildcard: the gateway, not the role, picks the model. */
	const roleModelOptions = useMemo(
		() =>
			AUTO_ROUTING_GATEWAYS.has(providerId)
				? [AUTO_ROUTING_MODEL, ...selectedModels]
				: selectedModels,
		[providerId, selectedModels],
	);

	/**
	 * Pre-check the roles this provider already serves, and re-seed whenever the provider changes.
	 * An auto-routing gateway being CREATED starts with every role checked — it serves any role out
	 * of the box, so the operator unchecks exceptions instead of ticking the full list by hand.
	 * Reopening a configured gateway keeps the stored assignment instead, even when that assignment
	 * is "no roles": deliberately unrouting the gateway is a configuration too.
	 */
	useEffect(() => {
		const preferring = rolePolicies.filter((policy) => rolePrefersProvider(policy, providerId));
		if (
			AUTO_ROUTING_GATEWAYS.has(providerId) &&
			preferring.length === 0 &&
			providerId !== initialProviderId
		) {
			setAssignedRoles(new Set(rolePolicies.map((policy) => policy.role)));
			return;
		}
		setAssignedRoles(new Set(preferring.map((policy) => policy.role)));
	}, [rolePolicies, providerId, initialProviderId]);

	useEffect(() => {
		setRoleModel((current) =>
			current && roleModelOptions.includes(current) ? current : (roleModelOptions[0] ?? ''),
		);
	}, [roleModelOptions]);

	if (!open || !entry) return null;

	const stepIndex = STEP_ORDER.indexOf(step);
	const stepLabels: Record<WizardStep, string> = {
		provider: t('app.providers.wizard.stepProvider', 'Choose provider'),
		credential: t('app.providers.wizard.stepCredential', 'Credential'),
		models: t('app.providers.wizard.stepModels', 'Select models'),
		validate: t('app.providers.wizard.stepValidate', 'Validate'),
		roles: t('app.providers.wizard.stepRoles', 'Assign roles'),
	};
	const auth = AUTH_META[entry.authKind];
	const cost = COST_META[costForModels(discovered)];
	/** Only offered while editing an account that already has a credential stored in the vault. */
	const keepCredentialOption =
		providerId === initialProviderId && initialCredentialRef.trim()
			? [
					{
						value: 'keep',
						label: t('app.providers.wizard.credModeKeep', 'Keep current'),
					},
				]
			: [];

	const selectProvider = (id: string) => {
		const selectedEntry = catalogEntry(id);
		const restoresInitialAccount = id === initialProviderId;
		const storedRef = restoresInitialAccount ? initialCredentialRef : '';
		setProviderId(id);
		setCredMode(defaultCredentialMode(selectedEntry, storedRef));
		setGeminiPricingMode(
			restoresInitialAccount && initialPricingMode === 'configured' ? 'configured' : 'free',
		);
		setGeminiFreeTierAttested(restoresInitialAccount && initialFreeTierAttested);
		clearApiKey();
		setCredentialRef(storedRef);
		setBaseUrl(
			(restoresInitialAccount ? initialBaseUrl : '') || (selectedEntry?.defaultBaseUrl ?? ''),
		);
		// Models synced for the previous provider must not survive the switch: a stale catalog would
		// mislabel the models step, suppress the auto-routing sync and steer role routing to model
		// ids the new provider does not serve.
		setDiscovered([]);
		setSelected(new Set());
		setValidation(null);
		setError('');
	};

	/** Create the vault credential (key mode) and enable the provider account with its base URL. */
	const persistProvider = async (): Promise<boolean> => {
		if (busy) return false;
		setBusy(true);
		setError('');
		try {
			if (entry.id === 'gemini' && geminiPricingMode === 'free' && !geminiFreeTierAttested) {
				setError(
					t(
						'app.providers.wizard.errorGeminiFreeAttestation',
						'Confirm that this Google project is on the Free tier before saving it as zero cost.',
					),
				);
				return false;
			}
			if (entry.needsBaseUrl && !baseUrl.trim()) {
				setError(t('app.providers.wizard.errorBaseUrl', 'Enter the provider base URL.'));
				return false;
			}
			let ref = credentialRef.trim();
			if (credMode === 'keep') {
				ref = initialCredentialRef.trim();
				if (!ref) {
					setError(
						t(
							'app.providers.wizard.errorCredentialMissing',
							'This provider has no stored credential yet; enter an API key or a reference.',
						),
					);
					return false;
				}
			}
			if (entry.authKind !== 'none' && credMode === 'ref' && !ref) {
				setError(t('app.providers.wizard.errorCredentialRef', 'Enter the credential reference.'));
				return false;
			}
			if (entry.authKind !== 'none' && credMode === 'key') {
				const apiKey = readApiKey().trim();
				if (!apiKey) {
					setError(t('app.providers.wizard.errorApiKey', 'Enter the API key.'));
					return false;
				}
				if (!writableBackend) {
					setError(
						t(
							'app.providers.wizard.errorNoBackend',
							'No writable credential backend is configured; use a reference instead.',
						),
					);
					return false;
				}
				const created = await createCredential(token, {
					label: `${entry.displayName} API key`,
					source: writableBackend,
					credentialRef: `aido/providers/${entry.id}`,
					authMode: 'token',
					value: apiKey,
				});
				ref = created.credential.credentialRef;
			}
			await createProviderAccountFromCatalog(token, {
				providerId: entry.id,
				enabled: true,
				...(entry.id === 'gemini'
					? {
							pricingMode: geminiPricingMode,
							metadata: {
								freeTierDeclaredByOperator: geminiPricingMode === 'free' && geminiFreeTierAttested,
							},
						}
					: {}),
				...(entry.id === 'omniroute'
					? {
							// The gateway runs on localhost but forwards to external providers; without this
							// marker AIDO classifies it as local and grants it local_private privacy it does
							// not have. Same marker scripts/setup_omniroute.py pins.
							metadata: { endpointKind: 'remote', gateway: 'omniroute' },
						}
					: {}),
				...(entry.needsBaseUrl ? { baseUrl: baseUrl.trim() } : {}),
				...(ref ? { credentialRef: ref } : {}),
			});
			clearApiKey();
			return true;
		} catch (saveError) {
			setError(errorMessage(saveError));
			return false;
		} finally {
			setBusy(false);
		}
	};

	const syncModels = async () => {
		if (busy) return;
		setBusy(true);
		setError('');
		try {
			const result = await syncProviderAccountModels(token, entry.id);
			const models = (result as { models?: ModelGatewayModel[] }).models ?? [];
			setDiscovered(models);
			setSelected(new Set(models.map((model) => model.id)));
		} catch (syncError) {
			setError(errorMessage(syncError));
		} finally {
			setBusy(false);
		}
	};

	const runValidate = async () => {
		if (busy) return;
		setBusy(true);
		setError('');
		try {
			await healthCheckModelGatewayProvider(token, entry.id);
			const model = discovered.find((item) => selected.has(item.id))?.model;
			const response = await testPromptModelGatewayProvider(
				token,
				entry.id,
				model ? { model } : {},
			);
			setValidation((response as { test: TestOutcome }).test);
		} catch (validateError) {
			setValidation({ ok: false, error: errorMessage(validateError) });
		} finally {
			setBusy(false);
		}
	};

	/**
	 * Route the chosen model to every checked role and stop routing the unchecked ones. A failure
	 * must surface — the provider is already saved, but the operator's routing intent was not
	 * applied — yet one role whose stored policy no longer validates (the PATCH revalidates the
	 * merged record) must not leave the remaining roles half-applied, so failures are collected per
	 * role and reported together; retrying only re-sends the roles still pending.
	 */
	const applyRoleAssignments = async () => {
		if (!roleModel) return;
		const failedRoles: string[] = [];
		for (const policy of rolePolicies) {
			const assign = assignedRoles.has(policy.role);
			if (!rolePreferredNeedsUpdate(policy, entry.id, roleModel, assign)) continue;
			try {
				await patchModelGatewayRolePolicy(token, policy.id, {
					preferred: nextPreferredCandidates(
						policy,
						entry.id,
						roleModel,
						assign,
					) as ModelGatewayRolePolicy['preferred'],
				});
			} catch {
				failedRoles.push(policy.role);
			}
		}
		if (failedRoles.length) {
			throw new Error(
				`${t(
					'app.providers.wizard.rolesPartialFailure',
					'Some role policies could not be updated:',
				)} ${failedRoles.join(', ')}`,
			);
		}
	};

	const finish = async () => {
		if (busy) return;
		setBusy(true);
		setError('');
		try {
			for (const model of discovered) {
				if (!selected.has(model.id)) {
					await patchModelGatewayModel(token, model.id, { enabled: false }).catch(() => undefined);
				}
			}
			await applyRoleAssignments();
			onSaved();
			onClose();
		} catch (finishError) {
			setError(errorMessage(finishError));
		} finally {
			setBusy(false);
		}
	};

	const goNext = async () => {
		if (step === 'provider') {
			setStep('credential');
			return;
		}
		if (step === 'credential') {
			if (await persistProvider()) {
				setStep('models');
				// The gateway owns model choice: sync for the operator instead of asking them to.
				if (AUTO_ROUTING_GATEWAYS.has(entry.id) && !discovered.length) void syncModels();
			}
			return;
		}
		if (step === 'models') {
			setStep('validate');
			return;
		}
		if (step === 'validate') {
			setStep('roles');
			return;
		}
	};

	const goBack = () => {
		setError('');
		if (stepIndex === 0) {
			onClose();
			return;
		}
		setStep(STEP_ORDER[Math.max(stepIndex - 1, 0)]);
	};

	const toggleModel = (id: string) => {
		setSelected((current) => {
			const next = new Set(current);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	};

	const toggleRole = (role: string) => {
		setAssignedRoles((current) => {
			const next = new Set(current);
			if (next.has(role)) next.delete(role);
			else next.add(role);
			return next;
		});
	};

	return (
		<Modal open={open} label={t('app.providers.wizard.title', 'Add provider')} onClose={onClose}>
			<div className="form-grid">
				<ol
					className="wizard-steps"
					aria-label={t('app.providers.wizard.stepsAria', 'Add provider steps')}
				>
					{STEP_ORDER.map((item, index) => (
						<li key={item} className={item === step ? 'current' : ''}>
							<span className="mono">0{index + 1}</span>
							<strong>{stepLabels[item]}</strong>
						</li>
					))}
				</ol>

				{step === 'provider' ? (
					<>
						<SelectField
							label={t('app.providers.wizard.provider', 'Provider')}
							value={providerId}
							onChange={(event) => selectProvider(event.target.value)}
							help={t(
								'app.providers.wizard.providerHelp',
								'Pick a provider to connect. CLI runtimes are set up from their card.',
							)}
						>
							{WIZARD_PROVIDERS.map((option) => (
								<option key={option.id} value={option.id}>
									{option.displayName}
								</option>
							))}
						</SelectField>
						<section
							className="stack compact"
							aria-label={t('app.providers.wizard.summaryAria', 'Provider summary')}
						>
							<div className="field">
								<span className="field-label">{t('app.providers.wizard.baseUrl', 'Base URL')}</span>
								<span className="mono">
									{entry.defaultBaseUrl ??
										t('app.providers.wizard.baseUrlOperator', 'You provide the endpoint')}
								</span>
							</div>
							<div className="inline">
								<span className="field-label">
									{t('app.providers.wizard.capabilities', 'Capabilities')}
								</span>
								{entry.capabilities.map((capability) => (
									<Badge tone="info" key={capability}>
										{capability}
									</Badge>
								))}
								<Badge tone={auth.tone}>{t(auth.labelKey, auth.fallback)}</Badge>
							</div>
						</section>
					</>
				) : null}

				{step === 'credential' ? (
					<>
						{entry.id === 'gemini' ? (
							<>
								<SegmentedControl
									label={t('app.providers.wizard.geminiTier', 'Gemini billing tier')}
									value={geminiPricingMode}
									onChange={(value) => {
										setGeminiPricingMode(value as GeminiPricingMode);
										setGeminiFreeTierAttested(false);
									}}
									options={[
										{
											value: 'free',
											label: t('app.providers.wizard.geminiTierFree', 'Free tier'),
										},
										{
											value: 'configured',
											label: t('app.providers.wizard.geminiTierPaid', 'Paid / configured'),
										},
									]}
								/>
								{geminiPricingMode === 'free' ? (
									<section
										className="stack compact"
										aria-label={t(
											'app.providers.wizard.geminiFreeNoticeAria',
											'Gemini free tier notice',
										)}
									>
										<Badge tone="warn">
											{t('app.providers.wizard.geminiFreeNotice', 'Google Free tier')}
										</Badge>
										<p className="field-help">
											{t(
												'app.providers.wizard.geminiFreeDataUse',
												'Google may use free-tier prompts and responses to improve its products. Do not send personal, sensitive, or confidential data.',
											)}
										</p>
										<p className="field-help">
											{t(
												'app.providers.wizard.geminiContextNotice',
												"1,048,576 tokens is Gemini 3.5 Flash's input context window per request, not a free quota. Free-tier limits vary by project and model.",
											)}
										</p>
										<Checkbox
											label={t(
												'app.providers.wizard.geminiFreeAttestation',
												'I verified in Google AI Studio that this project is on the Free tier. I understand AIDO cannot verify its billing state.',
											)}
											checked={geminiFreeTierAttested}
											onChange={() => setGeminiFreeTierAttested((current) => !current)}
										/>
									</section>
								) : (
									<p className="field-help">
										{t(
											'app.providers.wizard.geminiPaidNotice',
											'Use this option when billing is enabled for the Google AI project; configured paid rates may apply.',
										)}
									</p>
								)}
							</>
						) : null}
						{entry.authKind === 'api_key' ? (
							<SegmentedControl
								label={t('app.providers.wizard.credMode', 'Credential type')}
								value={credMode}
								onChange={(value) => setCredMode(value as CredentialMode)}
								options={[
									...keepCredentialOption,
									{ value: 'key', label: t('app.providers.wizard.credModeKey', 'API key') },
									{ value: 'ref', label: t('app.providers.wizard.credModeRef', 'Reference') },
								]}
							/>
						) : null}
						{entry.authKind === 'optional_api_key' ? (
							<SegmentedControl
								label={t('app.providers.wizard.credMode', 'Credential type')}
								value={credMode}
								onChange={(value) => setCredMode(value as CredentialMode)}
								options={[
									...keepCredentialOption,
									{
										value: 'none',
										label: t('app.providers.wizard.credModeNone', 'No credential'),
									},
									{ value: 'key', label: t('app.providers.wizard.credModeKey', 'API key') },
									{ value: 'ref', label: t('app.providers.wizard.credModeRef', 'Reference') },
								]}
							/>
						) : null}
						{credMode === 'keep' ? (
							<p className="field-help">
								{t(
									'app.providers.wizard.credentialKeepHelp',
									'Keeping the stored credential — change any other setting without re-entering the API key.',
								)}
							</p>
						) : null}

						{entry.needsBaseUrl ? (
							<TextField
								label={t('app.providers.wizard.baseUrl', 'Base URL')}
								value={baseUrl}
								autoComplete="off"
								placeholder="https://"
								onChange={(event) => setBaseUrl(event.target.value)}
								help={t(
									'app.providers.wizard.baseUrlHelp',
									'The provider endpoint for this account.',
								)}
							/>
						) : (
							<div className="field">
								<span className="field-label">{t('app.providers.wizard.baseUrl', 'Base URL')}</span>
								<span className="mono">
									{entry.defaultBaseUrl ?? t('app.providers.wizard.noBaseUrl', 'Not applicable')}
								</span>
								<span className="field-help">
									{t('app.providers.wizard.baseUrlPreset', 'Preconfigured — no URL needed.')}
								</span>
							</div>
						)}

						{entry.authKind !== 'none' && credMode === 'key' ? (
							<TextField
								ref={apiKeyRef}
								label={t('app.providers.wizard.apiKey', 'API key')}
								type="password"
								defaultValue=""
								autoComplete="new-password"
								help={t(
									'app.providers.wizard.apiKeyHelp',
									'Stored in the credential vault — never shown again.',
								)}
							/>
						) : null}

						{entry.authKind !== 'none' && credMode === 'ref' ? (
							<TextField
								label={t('app.providers.wizard.credentialRef', 'Credential reference')}
								value={credentialRef}
								autoComplete="off"
								placeholder="env:provider_api_key"
								onChange={(event) => setCredentialRef(event.target.value)}
								help={t(
									'app.providers.wizard.credentialRefHelp',
									'Reference an existing secret (env:, keyring:, openbao:, vault:).',
								)}
							/>
						) : null}

						{entry.authKind === 'none' ? (
							<p className="field-help">
								{t(
									'app.providers.wizard.noCredential',
									'This provider needs no API key; saving enables it.',
								)}
							</p>
						) : null}
						{entry.authKind === 'optional_api_key' && credMode === 'none' ? (
							<p className="field-help">
								{t(
									'app.providers.wizard.optionalCredential',
									'This provider can use a bearer token, but it is not required.',
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
									{discovered.length
										? `${selected.size}/${discovered.length} ${t('app.providers.wizard.modelsSelected', 'selected')}`
										: t('app.providers.wizard.modelsEmpty', 'No models synced yet.')}
								</span>
								<Badge tone={cost.tone}>
									<CircleDollarSign aria-hidden="true" size={12} />
									<span>{t(cost.labelKey, cost.fallback)}</span>
								</Badge>
							</div>
							<Button
								onClick={() => void syncModels()}
								loading={busy}
								icon={<RefreshCw size={15} />}
							>
								{t('app.providers.wizard.syncModels', 'Sync models')}
							</Button>
						</div>
						<div className="stack compact provider-model-list">
							{discovered.map((model) => (
								<Checkbox
									key={model.id}
									label={model.model}
									checked={selected.has(model.id)}
									onChange={() => toggleModel(model.id)}
								/>
							))}
						</div>
					</>
				) : null}

				{step === 'validate' ? (
					<>
						<Button
							variant="primary"
							onClick={() => void runValidate()}
							loading={busy}
							icon={<CheckCircle2 size={15} />}
						>
							{t('app.providers.wizard.runValidate', 'Validate connection')}
						</Button>
						{validation ? (
							<div className="inline" role="status">
								{validation.ok ? (
									<CheckCircle2 aria-hidden="true" size={15} />
								) : (
									<XCircle aria-hidden="true" size={15} />
								)}
								<Badge tone={validation.ok ? 'ok' : 'danger'}>
									{validation.ok
										? t('app.providers.wizard.validateOk', 'Provider responded')
										: t('app.providers.wizard.validateFailed', 'Validation failed')}
								</Badge>
								{validation.ok && typeof validation.latencyMs === 'number' ? (
									<span className="mono">{validation.latencyMs} ms</span>
								) : null}
								{validation.error ? <span className="field-help">{validation.error}</span> : null}
							</div>
						) : null}
					</>
				) : null}

				{step === 'roles' ? (
					roleModelOptions.length && rolePolicies.length ? (
						<>
							<SelectField
								label={t('app.providers.wizard.roleModel', 'Model for these roles')}
								value={roleModel}
								onChange={(event) => setRoleModel(event.target.value)}
								help={t(
									'app.providers.wizard.rolesHelp',
									'Checked roles route to this provider; clearing a role stops routing it here.',
								)}
							>
								{roleModelOptions.map((model) => (
									<option key={model} value={model}>
										{model === AUTO_ROUTING_MODEL
											? t(
													'app.providers.wizard.roleModelAuto',
													'Automatic — the gateway picks the model per request',
												)
											: model}
									</option>
								))}
							</SelectField>
							<div className="stack compact provider-model-list">
								{rolePolicies.map((policy) => (
									<Checkbox
										key={policy.id}
										label={policy.role}
										checked={assignedRoles.has(policy.role)}
										onChange={() => toggleRole(policy.role)}
									/>
								))}
							</div>
						</>
					) : (
						<p className="field-help">
							{rolePolicies.length
								? t(
										'app.providers.wizard.rolesNeedModels',
										'Sync and select at least one model to assign roles.',
									)
								: t('app.providers.wizard.rolesEmpty', 'No role policies are configured yet.')}
						</p>
					)
				) : null}

				{error ? (
					<div className="form-error" role="alert">
						{error}
					</div>
				) : null}

				<div className="wizard-actions">
					<Button
						onClick={goBack}
						disabled={busy}
						icon={stepIndex === 0 ? undefined : <Link2 size={14} />}
					>
						{stepIndex === 0
							? t('app.providers.wizard.cancel', 'Cancel')
							: t('app.providers.wizard.back', 'Back')}
					</Button>
					{step === 'roles' ? (
						<Button
							variant="primary"
							onClick={() => void finish()}
							loading={busy}
							icon={<KeyRound size={15} />}
						>
							{t('app.providers.wizard.finish', 'Save & finish')}
						</Button>
					) : (
						<Button variant="primary" onClick={() => void goNext()} loading={busy}>
							{t('app.providers.wizard.next', 'Next')}
						</Button>
					)}
				</div>
			</div>
		</Modal>
	);
}
