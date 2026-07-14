/**
 * Explicit NVIDIA model manifest and optional sourced token pricing. NVIDIA
 * visual/retrieval contracts cannot safely infer capabilities from one shared
 * `/models` response, so the operator records the exact endpoint/model facts.
 * @author Rodrigo Mason
 */
import { useEffect, useMemo, useState } from 'react';

import {
	createModelGatewayModel,
	createModelGatewayPricingSnapshot,
	patchModelGatewayProvider,
} from '../../api/client';
import type { ModelGatewayProviderAccount } from '../../api/types';
import { Badge, EmptyState } from '../../components/primitives';
import { Button, Checkbox, SelectField, TextField } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';

type CapabilityField =
	| 'supportsTools'
	| 'supportsJson'
	| 'supportsVision'
	| 'supportsReasoning'
	| 'supportsStreaming';

type PriceField =
	| 'inputPricePerMtok'
	| 'cachedInputPricePerMtok'
	| 'outputPricePerMtok'
	| 'reasoningPricePerMtok';

const EMPTY_CAPABILITIES: Record<CapabilityField, boolean> = {
	supportsTools: false,
	supportsJson: false,
	supportsVision: false,
	supportsReasoning: false,
	supportsStreaming: false,
};

const EMPTY_PRICES: Record<PriceField, string> = {
	inputPricePerMtok: '',
	cachedInputPricePerMtok: '',
	outputPricePerMtok: '',
	reasoningPricePerMtok: '',
};

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function optionalPrice(value: string, label: string): number | null {
	if (!value.trim()) return null;
	const parsed = Number(value);
	if (!Number.isFinite(parsed) || parsed < 0) throw new Error(`${label} must be zero or greater.`);
	return parsed;
}

export function NvidiaNimModelManifestPanel({
	providers,
	token,
	onSaved,
}: {
	providers: ModelGatewayProviderAccount[];
	token: string;
	onSaved: () => void;
}) {
	const { t } = useI18n();
	const nvidiaAccounts = useMemo(
		() =>
			providers
				.filter((provider) => provider.providerFamily === 'nvidia_nim')
				.sort((left, right) => left.providerId.localeCompare(right.providerId)),
		[providers],
	);
	const [providerId, setProviderId] = useState(nvidiaAccounts[0]?.providerId ?? '');
	const account = nvidiaAccounts.find((provider) => provider.providerId === providerId) ?? null;
	const [model, setModel] = useState('');
	const [displayName, setDisplayName] = useState('');
	const [contextWindow, setContextWindow] = useState('0');
	const [maxOutputTokens, setMaxOutputTokens] = useState('0');
	const [capabilities, setCapabilities] = useState<Record<CapabilityField, boolean>>({
		...EMPTY_CAPABILITIES,
	});
	const [prices, setPrices] = useState<Record<PriceField, string>>({ ...EMPTY_PRICES });
	const [pricingSource, setPricingSource] = useState('');
	const [freeTier, setFreeTier] = useState(false);
	const [enabled, setEnabled] = useState(true);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');
	const [success, setSuccess] = useState('');

	useEffect(() => {
		if (
			nvidiaAccounts.length === 0 ||
			nvidiaAccounts.some((item) => item.providerId === providerId)
		)
			return;
		setProviderId(nvidiaAccounts[0].providerId);
	}, [nvidiaAccounts, providerId]);

	// biome-ignore lint/correctness/useExhaustiveDependencies: reset endpoint-scoped pricing and capability claims when the selected account changes.
	useEffect(() => {
		setCapabilities({ ...EMPTY_CAPABILITIES });
		setPrices({ ...EMPTY_PRICES });
		setPricingSource('');
		setFreeTier(false);
		setError('');
		setSuccess('');
	}, [providerId]);

	const setCapability = (field: CapabilityField, value: boolean) => {
		setCapabilities((current) => ({ ...current, [field]: value }));
	};

	const setPrice = (field: PriceField, value: string) => {
		setPrices((current) => ({ ...current, [field]: value }));
	};

	const save = async () => {
		if (busy) return;
		setError('');
		setSuccess('');
		if (!account || !model.trim()) {
			setError(
				t(
					'app.modelGateway.nim.manifest.selectAccountAndModelId',
					'Choose an NVIDIA endpoint account and enter the exact provider model id.',
				),
			);
			return;
		}
		const context = Number(contextWindow);
		const maxOutput = Number(maxOutputTokens);
		if (
			!Number.isInteger(context) ||
			context < 0 ||
			!Number.isInteger(maxOutput) ||
			maxOutput < 0
		) {
			setError(
				t(
					'app.modelGateway.nim.manifest.tokenFieldsMustBeWholeNumbers',
					'Context window and max output tokens must be whole numbers greater than or equal to zero.',
				),
			);
			return;
		}
		if (account.apiFamily === 'chat_completions' && (context < 1 || maxOutput < 1)) {
			setError(
				t(
					'app.modelGateway.nim.manifest.chatRequiresPositiveLimits',
					'Chat manifests require a positive context window and max output token limit.',
				),
			);
			return;
		}
		if (account.deploymentMode === 'hosted_trial' && freeTier) {
			setError(
				t(
					'app.modelGateway.nim.manifest.trialCannotBeFreeTier',
					'NVIDIA hosted trial is evaluation access and cannot be declared as a production free tier.',
				),
			);
			return;
		}
		let parsedPrices: Record<PriceField, number | null>;
		try {
			parsedPrices = {
				inputPricePerMtok: optionalPrice(prices.inputPricePerMtok, 'Input price'),
				cachedInputPricePerMtok: optionalPrice(
					prices.cachedInputPricePerMtok,
					'Cached input price',
				),
				outputPricePerMtok: optionalPrice(prices.outputPricePerMtok, 'Output price'),
				reasoningPricePerMtok: optionalPrice(prices.reasoningPricePerMtok, 'Reasoning price'),
			};
		} catch (priceError) {
			setError(errorMessage(priceError));
			return;
		}
		const pricingDeclared = freeTier || Object.values(parsedPrices).some((value) => value !== null);
		if (pricingDeclared && !pricingSource.trim()) {
			setError(
				t(
					'app.modelGateway.nim.manifest.pricingSourceRequired',
					'A contract or official pricing source is required before prices can be applied.',
				),
			);
			return;
		}
		setBusy(true);
		let saveStage: 'model' | 'pricing' | 'account' = 'model';
		try {
			await createModelGatewayModel(token, {
				providerId: account.providerId,
				model: model.trim(),
				displayName: displayName.trim() || model.trim(),
				modelFamily: model.trim().split('/')[0] ?? 'nvidia_nim',
				apiFamily: account.apiFamily,
				contextWindow: context,
				maxOutputTokens: maxOutput,
				...capabilities,
				supportsEmbeddings: account.apiFamily === 'embeddings',
				supportsRerank: account.apiFamily === 'rerank',
				supportsImageGeneration: account.apiFamily === 'image_generation',
				supportsImageEditing: account.apiFamily === 'image_editing',
				...parsedPrices,
				freeTier,
				freeTierNotes:
					account.deploymentMode === 'hosted_trial'
						? 'NVIDIA hosted trial is evaluation access, not a production free tier.'
						: freeTier
							? `Declared from ${pricingSource.trim()}`
							: '',
				enabled,
				source: 'explicit_manifest:nvidia_panel',
			});
			if (pricingDeclared) {
				saveStage = 'pricing';
				await createModelGatewayPricingSnapshot(token, {
					providerId: account.providerId,
					model: model.trim(),
					...parsedPrices,
					freeTier,
					sourceRef: pricingSource.trim(),
					applyToCatalog: true,
					metadata: { configuredVia: 'nvidia_nim_model_manifest' },
				});
				if (account.deploymentMode !== 'hosted_trial' && account.pricingMode !== 'configured') {
					saveStage = 'account';
					await patchModelGatewayProvider(token, account.providerId, { pricingMode: 'configured' });
				}
			}
			setSuccess(
				t(
					'app.nvidiaNim.manifest.success',
					'Model manifest saved. Unknown prices remain null; sourced prices are active for cost admission.',
				),
			);
			onSaved();
		} catch (saveError) {
			const detail = errorMessage(saveError);
			setError(
				saveStage === 'model'
					? detail
					: saveStage === 'pricing'
						? `The model manifest was saved, but its pricing snapshot failed: ${detail}`
						: `The model and pricing snapshot were saved, but the endpoint pricing state was not updated: ${detail}`,
			);
		} finally {
			setBusy(false);
		}
	};

	return (
		<PanelShell title={t('app.nvidiaNim.manifest.title', 'NVIDIA model manifest and pricing')}>
			{nvidiaAccounts.length === 0 ? (
				<EmptyState
					title={t('app.nvidiaNim.manifest.empty', 'No NVIDIA endpoint account')}
					body={t(
						'app.nvidiaNim.manifest.emptyBody',
						'Create an endpoint-scoped NVIDIA account on the Providers tab first.',
					)}
				/>
			) : (
				<form
					className="form-grid"
					onSubmit={(event) => {
						event.preventDefault();
						void save();
					}}
				>
					<p className="muted">
						{t(
							'app.nvidiaNim.manifest.help',
							'Use exact model ids from NVIDIA documentation. Capabilities and prices are endpoint/model facts: leave them false or blank when not verified. Hosted trial remains evaluation-only and is never marked as a free tier.',
						)}
					</p>
					<div className="grid three">
						<SelectField
							label={t('app.nvidiaNim.manifest.endpoint', 'NVIDIA endpoint')}
							value={providerId}
							onChange={(event) => setProviderId(event.target.value)}
						>
							{nvidiaAccounts.map((provider) => (
								<option key={provider.providerId} value={provider.providerId}>
									{provider.displayName} · {provider.apiFamily} · {provider.deploymentMode}
								</option>
							))}
						</SelectField>
						<TextField
							label={t('app.nvidiaNim.manifest.model', 'Exact model id')}
							value={model}
							onChange={(event) => setModel(event.target.value)}
							placeholder="minimaxai/minimax-m3"
							required
						/>
						<TextField
							label={t('app.nvidiaNim.manifest.displayName', 'Model display name')}
							value={displayName}
							onChange={(event) => setDisplayName(event.target.value)}
						/>
						<TextField
							label={t('app.nvidiaNim.manifest.context', 'Context window')}
							type="number"
							min="0"
							value={contextWindow}
							onChange={(event) => setContextWindow(event.target.value)}
						/>
						<TextField
							label={t('app.nvidiaNim.manifest.maxOutput', 'Max output tokens')}
							type="number"
							min="0"
							value={maxOutputTokens}
							onChange={(event) => setMaxOutputTokens(event.target.value)}
						/>
					</div>
					<div className="inline">
						{(Object.keys(capabilities) as CapabilityField[]).map((field) => (
							<Checkbox
								key={field}
								label={field}
								checked={capabilities[field]}
								onChange={(event) => setCapability(field, event.target.checked)}
							/>
						))}
					</div>
					<div className="inline">
						<Badge tone={account?.deploymentMode === 'hosted_trial' ? 'warn' : 'info'}>
							{account?.deploymentMode === 'hosted_trial'
								? t('app.nvidiaNim.manifest.trialPricing', 'Trial pricing stays unknown')
								: t('app.nvidiaNim.manifest.sourcedPricing', 'Optional sourced USD / MTok pricing')}
						</Badge>
					</div>
					<div className="grid three">
						<TextField
							label={t('app.nvidiaNim.manifest.inputPrice', 'Input USD / MTok')}
							type="number"
							min="0"
							step="0.000001"
							value={prices.inputPricePerMtok}
							disabled={account?.deploymentMode === 'hosted_trial'}
							onChange={(event) => setPrice('inputPricePerMtok', event.target.value)}
						/>
						<TextField
							label={t('app.nvidiaNim.manifest.cachedPrice', 'Cached input USD / MTok')}
							type="number"
							min="0"
							step="0.000001"
							value={prices.cachedInputPricePerMtok}
							disabled={account?.deploymentMode === 'hosted_trial'}
							onChange={(event) => setPrice('cachedInputPricePerMtok', event.target.value)}
						/>
						<TextField
							label={t('app.nvidiaNim.manifest.outputPrice', 'Output USD / MTok')}
							type="number"
							min="0"
							step="0.000001"
							value={prices.outputPricePerMtok}
							disabled={account?.deploymentMode === 'hosted_trial'}
							onChange={(event) => setPrice('outputPricePerMtok', event.target.value)}
						/>
						<TextField
							label={t('app.nvidiaNim.manifest.reasoningPrice', 'Reasoning USD / MTok')}
							type="number"
							min="0"
							step="0.000001"
							value={prices.reasoningPricePerMtok}
							disabled={account?.deploymentMode === 'hosted_trial'}
							onChange={(event) => setPrice('reasoningPricePerMtok', event.target.value)}
						/>
						<TextField
							label={t('app.nvidiaNim.manifest.pricingSource', 'Pricing source / contract ref')}
							value={pricingSource}
							disabled={account?.deploymentMode === 'hosted_trial'}
							onChange={(event) => setPricingSource(event.target.value)}
							placeholder={t(
								'app.modelGateway.nim.manifest.pricingSourcePlaceholder',
								'https://... or contract reference',
							)}
						/>
					</div>
					<div className="inline">
						<Checkbox
							label={t('app.nvidiaNim.manifest.freeTier', 'Verified provider free tier')}
							checked={freeTier}
							disabled={account?.deploymentMode === 'hosted_trial'}
							onChange={(event) => setFreeTier(event.target.checked)}
							help={t(
								'app.nvidiaNim.manifest.freeTierHelp',
								'Use only with a source that explicitly states zero provider billing; local compute is not cost-free.',
							)}
						/>
						<Checkbox
							label={t('app.nvidiaNim.manifest.enabled', 'Enable model manifest')}
							checked={enabled}
							onChange={(event) => setEnabled(event.target.checked)}
						/>
					</div>
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
					<Button variant="primary" type="submit" loading={busy}>
						{t('app.nvidiaNim.manifest.save', 'Save model manifest')}
					</Button>
				</form>
			)}
		</PanelShell>
	);
}
