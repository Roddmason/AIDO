/**
 * "Test a model" for an API or gateway row of the thread's AI team panel. A gateway can expose
 * 1000+ enabled models, so the list loads only when opened, filters as the operator types and shows
 * a bounded number of matches; the chosen model goes to `validate-runtime` as `model`, so the test
 * checks what the operator will actually use instead of the backend's automatic sample.
 * @author Rodrigo Mason
 */
import { useEffect, useId, useMemo, useState } from 'react';

import { getModelGatewayModels } from '../../api/client';
import { Button, TextField } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

/** Matches rendered at once; the search narrows the rest. */
export const MODEL_PICKER_LIMIT = 50;

/** Enabled models of `providerId` whose id contains every word of `query` (case-insensitive). */
export function matchingModels(
	models: readonly string[],
	query: string,
	limit = MODEL_PICKER_LIMIT,
) {
	const words = query.toLowerCase().split(/\s+/).filter(Boolean);
	const matches = models.filter((model) => {
		const id = model.toLowerCase();
		return words.every((word) => id.includes(word));
	});
	return { shown: matches.slice(0, limit), total: matches.length };
}

export function RuntimeModelPicker({
	providerId,
	runtimeLabel,
	disabled,
	onTest,
}: {
	providerId: string;
	runtimeLabel: string;
	disabled: boolean;
	onTest: (model: string) => void;
}) {
	const { t } = useI18n();
	const listId = useId();
	const [open, setOpen] = useState(false);
	const [models, setModels] = useState<string[] | null>(null);
	const [failed, setFailed] = useState(false);
	const [query, setQuery] = useState('');
	const [picked, setPicked] = useState('');

	useEffect(() => {
		if (!open || models !== null) return undefined;
		const controller = new AbortController();
		getModelGatewayModels(controller.signal)
			.then((response) => {
				if (controller.signal.aborted) return;
				setModels(
					response.models
						.filter((item) => item.providerId === providerId && item.enabled)
						.map((item) => item.model)
						.sort((left, right) => left.localeCompare(right)),
				);
			})
			.catch(() => {
				if (!controller.signal.aborted) setFailed(true);
			});
		return () => controller.abort();
	}, [open, models, providerId]);

	const { shown, total } = useMemo(() => matchingModels(models ?? [], query), [models, query]);
	const selected = shown.includes(picked) ? picked : '';

	if (!open) {
		return (
			<div className="runtime-team-model-picker">
				<Button
					variant="secondary"
					disabled={disabled}
					aria-label={`${t('app.runtimeTeam.testModel', 'Test a model…')} ${runtimeLabel}`}
					onClick={() => setOpen(true)}
				>
					{t('app.runtimeTeam.testModel', 'Test a model…')}
				</Button>
			</div>
		);
	}
	return (
		<div className="runtime-team-model-picker">
			<TextField
				label={t('app.runtimeTeam.searchModels', 'Search enabled models')}
				value={query}
				onChange={(event) => setQuery(event.currentTarget.value)}
				autoComplete="off"
				help={
					models === null
						? failed
							? t('app.runtimeTeam.modelsLoadFailed', 'Could not load the models. Try again.')
							: t('app.runtimeTeam.modelsLoading', 'Loading models…')
						: t(
								'app.runtimeTeam.modelsShown',
								'Showing {shown} of {total} matches · {count} enabled models',
							)
								.replace('{shown}', String(shown.length))
								.replace('{total}', String(total))
								.replace('{count}', String(models.length))
				}
			/>
			<select
				id={listId}
				className="input runtime-team-model-list"
				size={Math.min(Math.max(shown.length, 2), 8)}
				aria-label={`${t('app.runtimeTeam.modelPicker', 'Model to test')} ${runtimeLabel}`}
				value={selected}
				onChange={(event) => setPicked(event.currentTarget.value)}
			>
				{shown.map((model) => (
					<option key={model} value={model}>
						{model}
					</option>
				))}
			</select>
			<div className="runtime-team-model-actions">
				<Button
					variant="primary"
					disabled={disabled || !selected}
					onClick={() => {
						onTest(selected);
						setOpen(false);
					}}
				>
					{t('app.runtimeTeam.testThisModel', 'Test this model')}
				</Button>
				<Button variant="secondary" onClick={() => setOpen(false)}>
					{t('app.runtimeTeam.cancelModelTest', 'Cancel')}
				</Button>
			</div>
		</div>
	);
}
