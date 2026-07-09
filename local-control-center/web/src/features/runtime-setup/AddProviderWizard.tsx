/**
 * "Add provider" wizard for the Providers & CLI setup catalog. A four-step modal — choose provider,
 * enter a required or optional credential reference (base URL only for custom/remote/Azure), sync
 * and pick models, then validate — that writes through the real control plane: it creates a vault
 * credential (secret in, never out), enables the provider account, discovers its models and runs a
 * real test prompt. The secret lives only in local state and is cleared the moment the provider is
 * saved.
 * @author Rodrigo Mason
 */

import { CheckCircle2, KeyRound, Link2, RefreshCw, XCircle } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
	createCredential,
	createProviderAccountFromCatalog,
	getCredentials,
	healthCheckModelGatewayProvider,
	patchModelGatewayModel,
	syncProviderAccountModels,
	testPromptModelGatewayProvider,
} from '../../api/client';
import type { CredentialBackend, ModelGatewayModel } from '../../api/types';
import { Badge, Modal } from '../../components/primitives';
import { Button, Checkbox, SegmentedControl, SelectField, TextField } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { catalogEntry, PROVIDER_CATALOG, type ProviderCatalogEntry } from './runtimeSetup';

type WizardStep = 'provider' | 'credential' | 'models' | 'validate';
const STEP_ORDER: WizardStep[] = ['provider', 'credential', 'models', 'validate'];

type TestOutcome = { ok: boolean; latencyMs?: number; sample?: string; error?: string | null };
type CredentialMode = 'none' | 'key' | 'ref';

/** Non-CLI providers the wizard can connect (CLI runtimes are set up via Detect & check on the card). */
const WIZARD_PROVIDERS = PROVIDER_CATALOG.filter((entry) => entry.group !== 'cli');

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function defaultCredentialMode(entry: ProviderCatalogEntry | undefined): CredentialMode {
	return entry?.authKind === 'optional_api_key' ? 'none' : 'key';
}

export function AddProviderWizard({
	open,
	token,
	initialProviderId,
	onClose,
	onSaved,
}: {
	open: boolean;
	token: string;
	initialProviderId?: string | null;
	onClose: () => void;
	onSaved: () => void;
}) {
	const { t } = useI18n();
	const [step, setStep] = useState<WizardStep>('provider');
	const [providerId, setProviderId] = useState<string>(
		initialProviderId ?? WIZARD_PROVIDERS[0]?.id ?? '',
	);
	const [credMode, setCredMode] = useState<CredentialMode>('key');
	const [apiKey, setApiKey] = useState('');
	const [credentialRef, setCredentialRef] = useState('');
	const [baseUrl, setBaseUrl] = useState('');
	const [backends, setBackends] = useState<CredentialBackend[]>([]);
	const [discovered, setDiscovered] = useState<ModelGatewayModel[]>([]);
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [validation, setValidation] = useState<TestOutcome | null>(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');

	const entry: ProviderCatalogEntry | undefined = catalogEntry(providerId);

	// biome-ignore lint/correctness/useExhaustiveDependencies: re-seed once per open; deriving from initialProviderId only.
	useEffect(() => {
		if (!open) return;
		const first = initialProviderId ?? WIZARD_PROVIDERS[0]?.id ?? '';
		const seedEntry = catalogEntry(first);
		setStep('provider');
		setProviderId(first);
		setCredMode(defaultCredentialMode(seedEntry));
		setApiKey('');
		setCredentialRef('');
		setBaseUrl(seedEntry?.defaultBaseUrl ?? '');
		setDiscovered([]);
		setSelected(new Set());
		setValidation(null);
		setError('');
		void getCredentials()
			.then((payload) => setBackends(payload.backends))
			.catch(() => setBackends([]));
	}, [open, initialProviderId]);

	const writableBackend = useMemo(
		() =>
			backends.find((backend) => backend.configured && !backend.readOnly && backend.default)
				?.kind ??
			backends.find((backend) => backend.configured && !backend.readOnly)?.kind ??
			'',
		[backends],
	);

	if (!open || !entry) return null;

	const stepIndex = STEP_ORDER.indexOf(step);
	const stepLabels: Record<WizardStep, string> = {
		provider: t('app.providers.wizard.stepProvider', 'Choose provider'),
		credential: t('app.providers.wizard.stepCredential', 'Credential'),
		models: t('app.providers.wizard.stepModels', 'Select models'),
		validate: t('app.providers.wizard.stepValidate', 'Validate'),
	};

	const selectProvider = (id: string) => {
		const selectedEntry = catalogEntry(id);
		setProviderId(id);
		setCredMode(defaultCredentialMode(selectedEntry));
		setApiKey('');
		setCredentialRef('');
		setBaseUrl(selectedEntry?.defaultBaseUrl ?? '');
		setError('');
	};

	/** Create the vault credential (key mode) and enable the provider account with its base URL. */
	const persistProvider = async (): Promise<boolean> => {
		if (busy) return false;
		setBusy(true);
		setError('');
		try {
			if (entry.needsBaseUrl && !baseUrl.trim()) {
				setError(t('app.providers.wizard.errorBaseUrl', 'Enter the provider base URL.'));
				return false;
			}
			let ref = credentialRef.trim();
			if (entry.authKind !== 'none' && credMode === 'ref' && !ref) {
				setError(t('app.providers.wizard.errorCredentialRef', 'Enter the credential reference.'));
				return false;
			}
			if (entry.authKind !== 'none' && credMode === 'key') {
				if (!apiKey.trim()) {
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
				...(entry.needsBaseUrl ? { baseUrl: baseUrl.trim() } : {}),
				...(ref ? { credentialRef: ref } : {}),
			});
			setApiKey('');
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

	const finish = async () => {
		if (busy) return;
		setBusy(true);
		try {
			for (const model of discovered) {
				if (!selected.has(model.id)) {
					await patchModelGatewayModel(token, model.id, { enabled: false }).catch(() => undefined);
				}
			}
			onSaved();
			onClose();
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
			if (await persistProvider()) setStep('models');
			return;
		}
		if (step === 'models') {
			setStep('validate');
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
				) : null}

				{step === 'credential' ? (
					<>
						{entry.authKind === 'api_key' ? (
							<SegmentedControl
								label={t('app.providers.wizard.credMode', 'Credential type')}
								value={credMode}
								onChange={(value) => setCredMode(value as CredentialMode)}
								options={[
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
									{
										value: 'none',
										label: t('app.providers.wizard.credModeNone', 'No credential'),
									},
									{ value: 'key', label: t('app.providers.wizard.credModeKey', 'API key') },
									{ value: 'ref', label: t('app.providers.wizard.credModeRef', 'Reference') },
								]}
							/>
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
								label={t('app.providers.wizard.apiKey', 'API key')}
								type="password"
								value={apiKey}
								autoComplete="new-password"
								onChange={(event) => setApiKey(event.target.value)}
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
							<span className="muted">
								{discovered.length
									? `${selected.size}/${discovered.length} ${t('app.providers.wizard.modelsSelected', 'selected')}`
									: t('app.providers.wizard.modelsEmpty', 'No models synced yet.')}
							</span>
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
					{step === 'validate' ? (
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
