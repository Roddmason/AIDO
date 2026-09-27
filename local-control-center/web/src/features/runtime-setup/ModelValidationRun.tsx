/**
 * "Validate all models" for an API/gateway provider: tests every enabled model one by one (queued
 * `models.validate_all_models`, bounded concurrency and time budget), shows a progress bar
 * (done/total, ok, failed) read from the model-validation status, then "N ok · M discarded" and a
 * filterable result list. Models that fail for good are discarded by the backend (disabled with
 * `validation_failed`, never deleted) only when the provider proved it works; each discarded row offers
 * Retest and Re-enable. Transient failures (429, 5xx, timeouts) stay enabled as "not confirmed".
 * The provider stays usable as long as one model passed. Each tested model costs one short completion.
 * @author Rodrigo Mason
 */

import { ListChecks, RotateCcw, Search, Square } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';

import {
	type CatalogValidationRequest,
	cancelExecution,
	getModelValidationStatus,
	type ModelValidationOutcome,
	type ModelValidationStatus,
	patchModelGatewayProviderModels,
	validateAllModels,
} from '../../api/client';
import { Button, StatusChip, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import { invalidateRuntimeTeamCache } from '../runtime-team/useRuntimeTeam';

/** Result rows mounted per page (same paging idea as the model picker). */
const PAGE_SIZE = 100;
/** Progress poll while a run is live; the status endpoint is a cheap SQLite read. */
const POLL_MS = 1000;

type Filter = 'all' | 'ok' | 'failed' | 'skipped';
type Translate = (key: string, fallback: string) => string;

function fill(template: string, values: Record<string, string | number>): string {
	return Object.entries(values).reduce(
		(text, [key, value]) => text.replaceAll(`{${key}}`, String(value)),
		template,
	);
}

function outcomeLabel(outcome: ModelValidationOutcome, t: Translate): string {
	if (outcome.status === 'ok') return t('app.providers.validateAll.status.ok', 'OK');
	if (outcome.status === 'skipped')
		return t('app.providers.validateAll.status.skipped', 'Not confirmed');
	return outcome.discarded
		? t('app.providers.validateAll.status.discarded', 'Discarded by validation')
		: t('app.providers.validateAll.status.failed', 'Failed (kept)');
}

function runNote(status: ModelValidationStatus, t: Translate): string | null {
	const run = status.run;
	if (!run) return null;
	switch (run.reason) {
		case 'no_model_passed':
			return t(
				'app.providers.validateAll.note.noModelPassed',
				'No model responded, so nothing was discarded: check the credential or the endpoint.',
			);
		case 'budget_exhausted':
			return fill(
				t(
					'app.providers.validateAll.note.budget',
					'Time budget reached: {count} model(s) were not tested yet.',
				),
				{ count: Math.max(0, run.total - run.done) },
			);
		case 'cancelled':
			return t('app.providers.validateAll.note.cancelled', 'Validation cancelled.');
		case 'provider_disabled':
			return t(
				'app.providers.validateAll.note.providerDisabled',
				'Stopped: the provider was switched off.',
			);
		case 'provider_quota_suspended':
			return t(
				'app.providers.validateAll.note.quota',
				'Stopped: the provider is suspended by usage quota.',
			);
		case 'provider_unreachable':
			return t(
				'app.providers.validateAll.note.unreachable',
				'Stopped: the endpoint did not answer; no model was discarded for it.',
			);
		default:
			return run.status === 'interrupted'
				? t('app.providers.validateAll.note.interrupted', 'The last validation was interrupted.')
				: null;
	}
}

export function ModelValidationRun({
	token,
	providerId,
	providerName,
	disabled = false,
	onChanged,
}: {
	token: string;
	providerId: string;
	providerName: string;
	/** Another card action is running, or the provider cannot be tested now. */
	disabled?: boolean;
	/** The catalog changed (models discarded, restored or re-enabled): the caller re-reads it. */
	onChanged?: () => void;
}) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [status, setStatus] = useState<ModelValidationStatus | null>(null);
	const [running, setRunning] = useState(false);
	const [executionId, setExecutionId] = useState<string | null>(null);
	const [queuedReason, setQueuedReason] = useState<string | null>(null);
	const [busyModel, setBusyModel] = useState<string | null>(null);
	const [open, setOpen] = useState(false);
	const [filter, setFilter] = useState<Filter>('all');
	const [query, setQuery] = useState('');
	const [limit, setLimit] = useState(PAGE_SIZE);
	const listId = useId();
	const mounted = useRef(true);

	const load = useCallback(async () => {
		try {
			const next = await getModelValidationStatus(providerId);
			if (mounted.current) setStatus(next);
			return next;
		} catch {
			return null; // read-only enrichment: the button keeps working without a status
		}
	}, [providerId]);

	useEffect(() => {
		mounted.current = true;
		void load();
		return () => {
			mounted.current = false;
		};
	}, [load]);

	const liveRun = status?.run?.status === 'running';
	const polling = running || liveRun;
	useEffect(() => {
		if (!polling) return;
		const timer = window.setInterval(() => void load(), POLL_MS);
		return () => window.clearInterval(timer);
	}, [polling, load]);

	const start = async (body: CatalogValidationRequest, model?: string) => {
		if (!token) {
			notify({
				title: t(
					'app.runtime.setup.tokenRequired',
					'A local write token is required to run runtime setup checks.',
				),
				tone: 'warn',
			});
			return;
		}
		if (model) setBusyModel(model);
		else setRunning(true);
		setQueuedReason(null);
		try {
			const result = await validateAllModels(token, providerId, body, undefined, (execution) => {
				setExecutionId(execution.executionId);
				setQueuedReason(
					execution.status === 'queued' || execution.status === 'resource_wait'
						? execution.reason || execution.status
						: null,
				);
			});
			invalidateRuntimeTeamCache();
			const run = result.run;
			notify({
				title: model
					? run.ok
						? t('app.providers.validateAll.retestOk', 'The model responded and is enabled again')
						: t('app.providers.validateAll.retestFailed', 'The model still fails')
					: fill(t('app.providers.validateAll.doneTitle', '{ok} ok · {discarded} discarded'), {
							ok: run.ok,
							discarded: run.discarded,
						}),
				body: model ? model : (runNote({ ...result, untested: 0 }, t) ?? providerName),
				tone: run.ok ? 'ok' : 'warn',
			});
		} catch (error) {
			notify({
				title: t('app.providers.validateAll.failed', 'Could not validate the models'),
				body: redactVisibleSecret(
					error instanceof Error ? error.message : String(error),
					'model validation failed',
				),
				tone: 'danger',
			});
		} finally {
			if (mounted.current) {
				setRunning(false);
				setBusyModel(null);
				setExecutionId(null);
				setQueuedReason(null);
			}
			await load();
			onChanged?.();
		}
	};

	const cancel = async () => {
		const id = executionId ?? status?.run?.runId;
		if (!id || !token) return;
		try {
			await cancelExecution(token, id, 'operator cancelled model validation');
		} catch (error) {
			notify({
				title: t('app.providers.validateAll.cancelFailed', 'Could not cancel the validation'),
				body: redactVisibleSecret(error instanceof Error ? error.message : String(error), ''),
				tone: 'danger',
			});
		}
	};

	const reenable = async (outcome: ModelValidationOutcome) => {
		if (!token) return;
		setBusyModel(outcome.model);
		try {
			await patchModelGatewayProviderModels(token, providerId, {
				enabled: true,
				models: [outcome.modelId],
			});
			invalidateRuntimeTeamCache();
			notify({
				title: t('app.providers.validateAll.reenabled', 'Model enabled again'),
				body: outcome.model,
				tone: 'ok',
			});
		} catch (error) {
			notify({
				title: t('app.providers.action.toggleFailed', 'Could not update the provider'),
				body: redactVisibleSecret(error instanceof Error ? error.message : String(error), ''),
				tone: 'danger',
			});
		} finally {
			if (mounted.current) setBusyModel(null);
			await load();
			onChanged?.();
		}
	};

	const outcomes = status?.outcomes ?? [];
	const counts = useMemo(() => {
		const tally = { ok: 0, failed: 0, skipped: 0, discarded: 0 };
		for (const outcome of outcomes) {
			tally[outcome.status] += 1;
			if (outcome.discarded) tally.discarded += 1;
		}
		return tally;
	}, [outcomes]);
	const needle = query.trim().toLocaleLowerCase();
	const filtered = useMemo(
		() =>
			outcomes.filter(
				(outcome) =>
					(filter === 'all' || outcome.status === filter) &&
					(!needle || outcome.model.toLocaleLowerCase().includes(needle)),
			),
		[outcomes, filter, needle],
	);
	const visible = filtered.slice(0, limit);

	const run = status?.run ?? null;
	const showProgress = (running || liveRun) && run !== null && run.status === 'running';
	const percent = run && run.total > 0 ? Math.round((run.done / run.total) * 100) : 0;
	const note = status ? runNote(status, t) : null;
	const untested = status?.untested ?? 0;
	const busy = running || liveRun || busyModel !== null;

	const filterLabels: Record<Filter, string> = {
		all: fill(t('app.providers.validateAll.filter.all', 'All ({count})'), {
			count: outcomes.length,
		}),
		ok: fill(t('app.providers.validateAll.filter.ok', 'OK ({count})'), { count: counts.ok }),
		failed: fill(t('app.providers.validateAll.filter.failed', 'Failed ({count})'), {
			count: counts.failed,
		}),
		skipped: fill(t('app.providers.validateAll.filter.skipped', 'Not confirmed ({count})'), {
			count: counts.skipped,
		}),
	};

	return (
		<section
			className="model-validation"
			aria-label={t('app.providers.validateAll.region', 'Model validation')}
		>
			<div className="model-validation-head">
				<span className="model-validation-title">
					<ListChecks aria-hidden="true" size={14} />
					{t('app.providers.validateAll.title', 'Model validation')}
				</span>
				{showProgress || running ? (
					<Button
						className="model-validation-action"
						icon={<Square size={13} />}
						disabled={!token || (!executionId && !run?.runId)}
						onClick={() => void cancel()}
					>
						{t('app.providers.validateAll.cancel', 'Cancel')}
					</Button>
				) : (
					<Button
						className="model-validation-action"
						disabled={disabled || busy}
						onClick={() => void start({})}
						title={t(
							'app.providers.validateAll.hint',
							'Sends one short test prompt to every enabled model; each one uses a little quota.',
						)}
					>
						{t('app.providers.validateAll.action', 'Validate all models')}
					</Button>
				)}
			</div>

			{showProgress && run ? (
				<div className="model-validation-progress">
					<div
						className="provider-usage-meter"
						role="progressbar"
						aria-label={t('app.providers.validateAll.progressAria', 'Models tested')}
						aria-valuemin={0}
						aria-valuemax={run.total}
						aria-valuenow={run.done}
					>
						<span className="provider-usage-fill" style={{ inlineSize: `${percent}%` }} />
					</div>
					<p className="model-validation-line tnum" role="status" aria-live="polite">
						{fill(
							t(
								'app.providers.validateAll.progress',
								'{done}/{total} tested · {ok} ok · {failed} failed',
							),
							{ done: run.done, total: run.total, ok: run.ok, failed: run.failed },
						)}
					</p>
				</div>
			) : running ? (
				<p className="model-validation-line" role="status" aria-live="polite">
					{queuedReason
						? fill(t('app.providers.validateAll.queued', 'Queued: {reason}'), {
								reason: queuedReason,
							})
						: t('app.providers.validateAll.starting', 'Starting…')}
				</p>
			) : run ? (
				<p className="model-validation-line tnum" role="status">
					<strong>
						{fill(t('app.providers.validateAll.summary', '{ok} ok · {discarded} discarded'), {
							ok: run.ok,
							discarded: run.discarded,
						})}
					</strong>
					{run.skipped ? (
						<span className="muted">
							{' · '}
							{fill(t('app.providers.validateAll.summarySkipped', '{count} not confirmed'), {
								count: run.skipped,
							})}
						</span>
					) : null}
				</p>
			) : null}

			{!busy && note ? <p className="field-help model-validation-note">{note}</p> : null}

			{!busy && untested > 0 && run ? (
				<Button
					className="model-validation-action"
					onClick={() => void start({ onlyUntested: true })}
				>
					{fill(t('app.providers.validateAll.continue', 'Test the {count} untested'), {
						count: untested,
					})}
				</Button>
			) : null}

			{outcomes.length ? (
				<button
					className="button model-validation-toggle"
					type="button"
					aria-expanded={open}
					aria-controls={listId}
					onClick={() => setOpen((value) => !value)}
				>
					{open
						? t('app.providers.validateAll.hideResults', 'Hide results')
						: fill(t('app.providers.validateAll.showResults', 'Show results ({count})'), {
								count: outcomes.length,
							})}
				</button>
			) : null}

			<div id={listId} className="model-picker model-validation-results" hidden={!open}>
				<div className="model-picker-toolbar">
					<label className="model-picker-search">
						<Search aria-hidden="true" size={15} />
						<input
							className="input"
							type="search"
							value={query}
							aria-label={t('app.providers.models.filterAria', 'Filter models')}
							placeholder={t('app.providers.models.filterPlaceholder', 'Filter by id or name')}
							onChange={(event) => {
								setQuery(event.target.value);
								setLimit(PAGE_SIZE);
							}}
						/>
					</label>
				</div>
				<fieldset
					className="model-validation-filters"
					aria-label={t('app.providers.validateAll.filterAria', 'Show results')}
				>
					{(['all', 'ok', 'failed', 'skipped'] as const).map((value) => (
						<button
							key={value}
							type="button"
							className="button model-validation-filter"
							aria-pressed={filter === value}
							onClick={() => {
								setFilter(value);
								setLimit(PAGE_SIZE);
							}}
						>
							{filterLabels[value]}
						</button>
					))}
				</fieldset>
				<ul className="model-validation-list">
					{visible.map((outcome) => (
						<li
							key={outcome.modelId}
							className="model-validation-row"
							data-status={outcome.discarded ? 'discarded' : outcome.status}
						>
							<div className="model-validation-row-head">
								<span className="mono model-validation-model">{outcome.model}</span>
								<StatusChip
									tone={
										outcome.status === 'ok'
											? 'ok'
											: outcome.status === 'skipped'
												? 'warn'
												: 'danger'
									}
								>
									{outcomeLabel(outcome, t)}
								</StatusChip>
							</div>
							{outcome.status !== 'ok' ? (
								<p className="field-help model-validation-reason" title={outcome.detail ?? ''}>
									{outcome.detail || outcome.reason}
								</p>
							) : null}
							{outcome.status !== 'ok' ? (
								<div className="model-validation-row-actions">
									<Button
										className="model-validation-action"
										icon={<RotateCcw size={13} />}
										disabled={busy}
										loading={busyModel === outcome.model}
										aria-label={fill(t('app.providers.validateAll.retestAria', 'Retest {model}'), {
											model: outcome.model,
										})}
										onClick={() => void start({ models: [outcome.modelId] }, outcome.model)}
									>
										{t('app.providers.validateAll.retest', 'Retest')}
									</Button>
									{outcome.discarded ? (
										<Button
											className="model-validation-action"
											disabled={busy}
											aria-label={fill(
												t('app.providers.validateAll.reenableAria', 'Re-enable {model}'),
												{ model: outcome.model },
											)}
											onClick={() => void reenable(outcome)}
										>
											{t('app.providers.validateAll.reenable', 'Re-enable')}
										</Button>
									) : null}
								</div>
							) : null}
						</li>
					))}
				</ul>
				{filtered.length === 0 ? (
					<p className="field-help model-picker-empty">
						{t('app.providers.validateAll.noMatch', 'No result matches this filter.')}
					</p>
				) : null}
				{filtered.length > visible.length ? (
					<div className="model-picker-more">
						<span className="field-help tnum">
							{fill(t('app.providers.models.showingOf', 'Showing {shown} of {total}'), {
								shown: visible.length,
								total: filtered.length,
							})}
						</span>
						<Button onClick={() => setLimit((current) => current + PAGE_SIZE)}>
							{fill(t('app.providers.models.showMore', 'Show {count} more'), {
								count: Math.min(PAGE_SIZE, filtered.length - visible.length),
							})}
						</Button>
					</div>
				) : null}
			</div>
		</section>
	);
}
