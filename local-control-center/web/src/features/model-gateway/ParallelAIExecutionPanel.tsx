/**
 * Bounded one-or-many chat execution console. Every branch is explicit and the
 * backend owns quota admission, concurrency and auditable settlement.
 * @author Rodrigo Mason
 */
import { useEffect, useMemo, useState } from 'react';

import { executeModelGatewayAIExecution } from '../../api/client';
import type {
	ModelGatewayAIExecutionPlan,
	ModelGatewayAIExecutionResult,
	ModelGatewayModel,
	ModelGatewayProviderAccount,
	Project,
} from '../../api/types';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import { Button, SelectField, TextArea, TextField } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { money, text } from './utils';

type Strategy = NonNullable<ModelGatewayAIExecutionPlan['strategy']>;
type Branch = { key: number; providerId: string; model: string; maxTokens: string };

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function firstModel(models: ModelGatewayModel[], providerId: string): string {
	return (
		models.find(
			(model) =>
				model.providerId === providerId && model.apiFamily === 'chat_completions' && model.enabled,
		)?.model ?? ''
	);
}

export function ParallelAIExecutionPanel({
	projects,
	providers,
	models,
	token,
}: {
	projects: Project[];
	providers: ModelGatewayProviderAccount[];
	models: ModelGatewayModel[];
	token: string;
}) {
	const { t } = useI18n();
	const chatProviders = useMemo(
		() =>
			providers
				.filter((provider) => provider.enabled && provider.apiFamily === 'chat_completions')
				.sort((left, right) => left.providerId.localeCompare(right.providerId)),
		[providers],
	);
	const initialProvider = chatProviders[0]?.providerId ?? '';
	const [nextKey, setNextKey] = useState(2);
	const [projectId, setProjectId] = useState(projects[0]?.id ?? '');
	const [strategy, setStrategy] = useState<Strategy>('single');
	const [systemPrompt, setSystemPrompt] = useState('');
	const [prompt, setPrompt] = useState('');
	const [temperature, setTemperature] = useState('');
	const [maxParallelism, setMaxParallelism] = useState('1');
	const [minSuccessful, setMinSuccessful] = useState('1');
	const [branches, setBranches] = useState<Branch[]>([
		{
			key: 1,
			providerId: initialProvider,
			model: firstModel(models, initialProvider),
			maxTokens: '1024',
		},
	]);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');
	const [result, setResult] = useState<ModelGatewayAIExecutionResult | null>(null);

	useEffect(() => {
		if (!projectId && projects[0]?.id) setProjectId(projects[0].id);
	}, [projectId, projects]);

	useEffect(() => {
		if (!initialProvider) return;
		setBranches((current) =>
			current.map((branch) => {
				if (chatProviders.some((provider) => provider.providerId === branch.providerId)) {
					const discoveredModel = firstModel(models, branch.providerId);
					return !branch.model && discoveredModel ? { ...branch, model: discoveredModel } : branch;
				}
				return {
					...branch,
					providerId: initialProvider,
					model: firstModel(models, initialProvider),
				};
			}),
		);
	}, [chatProviders, initialProvider, models]);

	const updateBranch = (key: number, patch: Partial<Branch>) => {
		setBranches((current) =>
			current.map((branch) => (branch.key === key ? { ...branch, ...patch } : branch)),
		);
		setResult(null);
	};

	const addBranch = () => {
		if (branches.length >= 8) return;
		const provider = chatProviders.find((candidate) => {
			const model = firstModel(models, candidate.providerId);
			return (
				model &&
				!branches.some(
					(branch) => branch.providerId === candidate.providerId && branch.model === model,
				)
			);
		});
		const providerId = provider?.providerId ?? initialProvider;
		setBranches((current) => [
			...current,
			{
				key: nextKey,
				providerId,
				model: firstModel(models, providerId),
				maxTokens: '1024',
			},
		]);
		setNextKey((current) => current + 1);
		if (strategy === 'single') setStrategy('parallel_compare');
		setMaxParallelism(String(Math.min(8, branches.length + 1)));
		setResult(null);
	};

	const removeBranch = (key: number) => {
		if (branches.length === 1) return;
		const next = branches.filter((branch) => branch.key !== key);
		setBranches(next);
		setMaxParallelism(String(Math.min(Number(maxParallelism) || 1, next.length)));
		if (strategy === 'quorum') {
			setMinSuccessful(String(Math.min(Math.max(2, Number(minSuccessful) || 2), next.length)));
		}
		if (next.length === 1) {
			setStrategy('single');
			setMinSuccessful('1');
			setMaxParallelism('1');
		}
		setResult(null);
	};

	const changeStrategy = (next: Strategy) => {
		setStrategy(next);
		if (next === 'single') {
			setBranches((current) => current.slice(0, 1));
			setMinSuccessful('1');
			setMaxParallelism('1');
		} else if (next === 'parallel_compare') {
			setMinSuccessful('1');
		} else {
			setMinSuccessful(String(Math.min(2, branches.length)));
		}
		setResult(null);
	};

	const execute = async () => {
		if (busy) return;
		setError('');
		setResult(null);
		if (!projectId || !prompt.trim()) {
			setError(
				t(
					'app.modelGateway.parallel.projectAndPromptRequired',
					'Project and user prompt are required.',
				),
			);
			return;
		}
		if (chatProviders.length === 0) {
			setError(
				t(
					'app.modelGateway.parallel.noEnabledEndpoint',
					'No enabled chat-completions endpoint account is available.',
				),
			);
			return;
		}
		const parallelism = Number(maxParallelism);
		const quorum = Number(minSuccessful);
		if (!Number.isInteger(parallelism) || parallelism < 1 || parallelism > branches.length) {
			setError(
				t(
					'app.modelGateway.parallel.maxParallelismRange',
					'Max parallelism must be between 1 and the branch count.',
				),
			);
			return;
		}
		if (
			!Number.isInteger(quorum) ||
			quorum < 1 ||
			quorum > branches.length ||
			(strategy === 'quorum' && (branches.length < 2 || quorum < 2))
		) {
			setError(
				t(
					'app.modelGateway.parallel.quorumRequiresTwo',
					'Quorum requires at least two branches and two successful results.',
				),
			);
			return;
		}
		let branchPayload: ModelGatewayAIExecutionPlan['branches'];
		try {
			branchPayload = branches.map((branch) => {
				const maxTokens = Number(branch.maxTokens);
				if (!branch.providerId || !branch.model.trim()) {
					throw new Error('Every branch needs an endpoint and model.');
				}
				if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 131_072) {
					throw new Error('Each max tokens value must be a whole number from 1 to 131072.');
				}
				return {
					providerId: branch.providerId,
					model: branch.model.trim(),
					maxTokens,
				};
			});
		} catch (validationError) {
			setError(errorMessage(validationError));
			return;
		}
		if (
			new Set(branchPayload.map((branch) => `${branch.providerId}\u0000${branch.model}`)).size !==
			branchPayload.length
		) {
			setError(
				t(
					'app.modelGateway.parallel.branchesMustBeUnique',
					'Endpoint/model branches must be unique.',
				),
			);
			return;
		}
		const parsedTemperature = temperature.trim() ? Number(temperature) : null;
		if (
			parsedTemperature !== null &&
			(!Number.isFinite(parsedTemperature) || parsedTemperature < 0 || parsedTemperature > 2)
		) {
			setError(
				t('app.modelGateway.parallel.temperatureRange', 'Temperature must be between 0 and 2.'),
			);
			return;
		}
		setBusy(true);
		try {
			const response = await executeModelGatewayAIExecution(token, {
				projectId,
				strategy,
				messages: [
					...(systemPrompt.trim()
						? [{ role: 'system' as const, content: systemPrompt.trim() }]
						: []),
					{ role: 'user', content: prompt.trim() },
				],
				branches: branchPayload,
				maxParallelism: parallelism,
				minSuccessful: strategy === 'single' || strategy === 'parallel_compare' ? 1 : quorum,
				temperature: parsedTemperature,
			});
			setResult(response.execution);
		} catch (executionError) {
			setError(errorMessage(executionError));
		} finally {
			setBusy(false);
		}
	};

	return (
		<PanelShell title={t('app.parallelExecution.title', 'One-or-many model execution')}>
			<div className="stack">
				<p className="muted">
					{t(
						'app.parallelExecution.help',
						'Branches are explicit and admitted against endpoint/model quota leases before network calls. Parallel compare succeeds with one branch; quorum requires the configured minimum. Prompts are not persisted, but each selected provider receives them.',
					)}
				</p>
				{chatProviders.length === 0 ? (
					<EmptyState
						title={t('app.parallelExecution.noEndpoints', 'No executable chat endpoints')}
						body={t(
							'app.parallelExecution.noEndpointsBody',
							'Create and enable a chat-completions endpoint, configure its model manifest and add a limit policy first.',
						)}
					/>
				) : (
					<form
						className="form-grid"
						onSubmit={(event) => {
							event.preventDefault();
							void execute();
						}}
					>
						<div className="grid three">
							<SelectField
								label={t('app.parallelExecution.project', 'Project')}
								value={projectId}
								onChange={(event) => setProjectId(event.target.value)}
								required
							>
								{projects.map((project) => (
									<option key={project.id} value={project.id}>
										{project.name} ({project.status})
									</option>
								))}
							</SelectField>
							<SelectField
								label={t('app.parallelExecution.strategy', 'Strategy')}
								value={strategy}
								onChange={(event) => changeStrategy(event.target.value as Strategy)}
							>
								<option value="single">single</option>
								<option value="parallel_compare">parallel_compare</option>
								<option value="quorum">quorum</option>
							</SelectField>
							<TextField
								label={t('app.parallelExecution.temperature', 'Temperature')}
								type="number"
								min="0"
								max="2"
								step="0.1"
								value={temperature}
								onChange={(event) => setTemperature(event.target.value)}
								help={t(
									'app.parallelExecution.temperatureHelp',
									'Blank uses the provider default.',
								)}
							/>
							<TextField
								label={t('app.parallelExecution.maxParallelism', 'Max parallelism')}
								type="number"
								min="1"
								max={branches.length}
								value={maxParallelism}
								onChange={(event) => setMaxParallelism(event.target.value)}
							/>
							<TextField
								label={t('app.parallelExecution.minSuccessful', 'Minimum successful')}
								type="number"
								min={strategy === 'quorum' ? 2 : 1}
								max={branches.length}
								value={minSuccessful}
								disabled={strategy !== 'quorum'}
								onChange={(event) => setMinSuccessful(event.target.value)}
							/>
						</div>
						<TextArea
							label={t('app.parallelExecution.systemPrompt', 'System message (optional)')}
							value={systemPrompt}
							onChange={(event) => setSystemPrompt(event.target.value)}
							rows={3}
						/>
						<TextArea
							label={t('app.parallelExecution.prompt', 'User prompt')}
							value={prompt}
							onChange={(event) => setPrompt(event.target.value)}
							rows={5}
							required
						/>
						<div className="stack">
							{branches.map((branch, index) => {
								const availableModels = models.filter(
									(model) =>
										model.providerId === branch.providerId &&
										model.apiFamily === 'chat_completions' &&
										model.enabled,
								);
								const listId = `parallel-execution-models-${branch.key}`;
								return (
									<div className="card card--static" key={branch.key}>
										<div className="card-header">
											<strong className="card-title">
												{t('app.parallelExecution.branch', 'Branch')} {index + 1}
											</strong>
											{branches.length > 1 ? (
												<Button onClick={() => removeBranch(branch.key)}>
													{t('app.parallelExecution.removeBranch', 'Remove')}
												</Button>
											) : null}
										</div>
										<div className="grid three">
											<SelectField
												label={t('app.parallelExecution.endpoint', 'Endpoint')}
												value={branch.providerId}
												onChange={(event) => {
													const nextProvider = event.target.value;
													updateBranch(branch.key, {
														providerId: nextProvider,
														model: firstModel(models, nextProvider),
													});
												}}
											>
												{chatProviders.map((provider) => (
													<option key={provider.providerId} value={provider.providerId}>
														{provider.displayName} ({provider.providerId})
													</option>
												))}
											</SelectField>
											<TextField
												label={t('ui.static.model.68c2cc7f', 'Model')}
												value={branch.model}
												onChange={(event) =>
													updateBranch(branch.key, { model: event.target.value })
												}
												list={listId}
												required
												help={t(
													'app.parallelExecution.modelHelp',
													'Choose a manifest model or enter its exact provider id.',
												)}
											/>
											<datalist id={listId}>
												{availableModels.map((model) => (
													<option key={model.id} value={model.model} />
												))}
											</datalist>
											<TextField
												label={t('app.parallelExecution.maxTokens', 'Max output tokens')}
												type="number"
												min="1"
												max="131072"
												value={branch.maxTokens}
												onChange={(event) =>
													updateBranch(branch.key, { maxTokens: event.target.value })
												}
											/>
										</div>
									</div>
								);
							})}
						</div>
						<div className="inline">
							<Button onClick={addBranch} disabled={branches.length >= 8}>
								{t('app.parallelExecution.addBranch', 'Add model branch')}
							</Button>
							<Button variant="primary" type="submit" loading={busy}>
								{t('app.parallelExecution.execute', 'Execute admitted plan')}
							</Button>
						</div>
						{error ? (
							<div className="form-error" role="alert">
								{error}
							</div>
						) : null}
					</form>
				)}

				{result ? (
					<div className="stack" role="status" aria-live="polite">
						<div className="inline">
							<Badge tone={result.succeeded ? 'ok' : 'warn'}>{result.status}</Badge>
							<span className="mono">{result.executionId}</span>
							<span>
								{result.successfulBranches}/{result.branches.length}{' '}
								{t('app.parallelExecution.succeeded', 'branches succeeded')}
							</span>
						</div>
						<DataTable
							rows={result.branches}
							empty={
								<EmptyState
									title={t('app.modelGateway.parallel.noBranchResultsTitle', 'No branch results')}
									body={t(
										'app.modelGateway.parallel.noBranchResultsBody',
										'The execution returned no branches.',
									)}
								/>
							}
							columns={[
								{
									key: 'branch',
									label: t('app.modelGateway.parallel.endpointModelColumn', 'Endpoint / model'),
									render: (row) => (
										<div className="stack compact">
											<span className="mono">{row.providerId}</span>
											<span>{row.model}</span>
										</div>
									),
								},
								{
									key: 'status',
									label: 'Status',
									render: (row) => (
										<Badge tone={row.status === 'completed' ? 'ok' : 'warn'}>{row.status}</Badge>
									),
								},
								{
									key: 'tokens',
									label: t('app.modelGateway.parallel.tokensColumn', 'Input / output / total'),
									render: (row) =>
										`${text(row.inputTokens)} / ${text(row.outputTokens)} / ${text(row.totalTokens)}`,
								},
								{
									key: 'cost',
									label: 'Actual cost',
									render: (row) => (
										<div className="stack compact">
											<span>{money(row.actualCostUsd, 'unknown')}</span>
											<span className="muted">{row.costStatus}</span>
										</div>
									),
								},
								{
									key: 'latency',
									label: t('app.modelGateway.parallel.latencyColumn', 'Latency ms'),
									render: (row) => text(row.latencyMs),
								},
								{
									key: 'output',
									label: t('app.modelGateway.parallel.outputErrorColumn', 'Output / error'),
									render: (row) =>
										row.content ? (
											<details>
												<summary>{t('app.parallelExecution.showOutput', 'Show output')}</summary>
												<pre className="artifact-preview">{row.content}</pre>
											</details>
										) : (
											text(row.errorCode)
										),
								},
							]}
						/>
					</div>
				) : null}
			</div>
		</PanelShell>
	);
}
