/**
 * Model picker that stays usable with gateway catalogs of 1000+ models (OmniRoute announces every
 * upstream it can route). A toolbar filters by id or name, states "N selected of M" (and "K shown"
 * while filtered) and offers bulk actions: select all / none over the whole catalog, select / clear
 * the shown (filtered) set, and "Select recommended" when the gateway ships a curated allowlist. A
 * tri-state checkbox mirrors the shown set (all / none / some). Rows render in pages of
 * {@link PAGE_SIZE} with "Show more", so a huge catalog never mounts thousands of inputs at once.
 * Selection is owned by the caller: this component only reports the next set.
 * @author Rodrigo Mason
 */

import { ListChecks, Search, Sparkles } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';

import { Button, Checkbox } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

/** Rows mounted per page; 200 checkboxes stay smooth even on modest Windows laptops. */
export const PAGE_SIZE = 200;

export type SelectableModel = {
	/** Catalog id (`provider:model`), the selection key. */
	id: string;
	/** Visible label and accessible name of the row checkbox. */
	label: string;
	/** Extra searchable text (display name); the id and label are always searched. */
	searchText?: string;
};

export function ModelSelectionList({
	models,
	selected,
	onChange,
	recommended,
	disabled = false,
}: {
	models: SelectableModel[];
	selected: ReadonlySet<string>;
	onChange: (next: Set<string>) => void;
	/** Ids of the curated allowlist; the "Select recommended" action appears only when non-empty. */
	recommended?: ReadonlySet<string> | null;
	disabled?: boolean;
}) {
	const { t } = useI18n();
	const [query, setQuery] = useState('');
	const [limit, setLimit] = useState(PAGE_SIZE);
	const headerRef = useRef<HTMLInputElement>(null);
	const listId = useId();

	/** Lower-cased haystack per model, computed once per catalog instead of on every keystroke. */
	const haystacks = useMemo(
		() =>
			models.map((model) =>
				`${model.id}\n${model.label}\n${model.searchText ?? ''}`.toLocaleLowerCase(),
			),
		[models],
	);
	const needle = query.trim().toLocaleLowerCase();
	const filtered = useMemo(
		() => (needle ? models.filter((_, index) => haystacks[index].includes(needle)) : models),
		[models, haystacks, needle],
	);
	const filtering = needle.length > 0;
	const shownSelected = useMemo(
		() => filtered.reduce((count, model) => count + (selected.has(model.id) ? 1 : 0), 0),
		[filtered, selected],
	);
	const headerState: 'all' | 'none' | 'some' =
		filtered.length > 0 && shownSelected === filtered.length
			? 'all'
			: shownSelected === 0
				? 'none'
				: 'some';

	// `indeterminate` has no HTML attribute: it is set on the element so the header reads "mixed".
	useEffect(() => {
		if (headerRef.current) headerRef.current.indeterminate = headerState === 'some';
	}, [headerState]);

	const recommendedInCatalog = useMemo(
		() => (recommended ? models.filter((model) => recommended.has(model.id)) : []),
		[models, recommended],
	);

	const setMany = (ids: Iterable<string>, value: boolean, base: ReadonlySet<string> = selected) => {
		const next = new Set(base);
		for (const id of ids) {
			if (value) next.add(id);
			else next.delete(id);
		}
		onChange(next);
	};
	const shownIds = () => filtered.map((model) => model.id);
	const toggle = (id: string) => setMany([id], !selected.has(id));

	const visible = filtered.slice(0, limit);
	const remaining = filtered.length - visible.length;

	const countLabel = t('app.providers.models.countSelected', '{selected} selected of {total}')
		.replace('{selected}', String(selected.size))
		.replace('{total}', String(models.length));
	const shownLabel = t('app.providers.models.countShown', '{shown} shown').replace(
		'{shown}',
		String(filtered.length),
	);

	return (
		<div className="model-picker">
			<div className="model-picker-toolbar">
				<label className="model-picker-search">
					<Search aria-hidden="true" size={15} />
					<input
						className="input"
						type="search"
						value={query}
						disabled={disabled}
						aria-controls={listId}
						aria-label={t('app.providers.models.filterAria', 'Filter models')}
						placeholder={t('app.providers.models.filterPlaceholder', 'Filter by id or name')}
						onChange={(event) => {
							setQuery(event.target.value);
							setLimit(PAGE_SIZE);
						}}
					/>
				</label>
				<p className="model-picker-count" role="status" aria-live="polite">
					<span className="tnum">{countLabel}</span>
					{filtering ? <span className="muted tnum"> · {shownLabel}</span> : null}
				</p>
			</div>

			<div className="model-picker-bulk">
				<label className="checkbox-row model-picker-header">
					<input
						ref={headerRef}
						type="checkbox"
						checked={headerState === 'all'}
						disabled={disabled || filtered.length === 0}
						aria-controls={listId}
						onChange={() => setMany(shownIds(), headerState !== 'all')}
					/>
					<span>
						{filtering
							? t('app.providers.models.toggleShown', 'All shown')
							: t('app.providers.models.toggleAll', 'All models')}
					</span>
				</label>
				<div className="model-picker-actions">
					<Button
						className="model-picker-action"
						disabled={disabled || selected.size === models.length}
						icon={<ListChecks size={14} />}
						onClick={() =>
							setMany(
								models.map((model) => model.id),
								true,
							)
						}
					>
						{t('app.providers.models.selectAll', 'Select all')}
					</Button>
					<Button
						className="model-picker-action"
						disabled={disabled || selected.size === 0}
						onClick={() => onChange(new Set())}
					>
						{t('app.providers.models.selectNone', 'Select none')}
					</Button>
					{filtering ? (
						<>
							<Button
								className="model-picker-action"
								disabled={disabled || headerState === 'all'}
								onClick={() => setMany(shownIds(), true)}
							>
								{t('app.providers.models.selectShown', 'Select shown')}
							</Button>
							<Button
								className="model-picker-action"
								disabled={disabled || shownSelected === 0}
								onClick={() => setMany(shownIds(), false)}
							>
								{t('app.providers.models.clearShown', 'Clear shown')}
							</Button>
						</>
					) : null}
					{recommendedInCatalog.length ? (
						<Button
							className="model-picker-action"
							disabled={disabled}
							icon={<Sparkles size={14} />}
							title={t(
								'app.providers.models.selectRecommendedHint',
								'Keep only the curated models this gateway is known to serve.',
							)}
							onClick={() =>
								setMany(
									recommendedInCatalog.map((model) => model.id),
									true,
									new Set(),
								)
							}
						>
							{t('app.providers.models.selectRecommended', 'Select recommended ({count})').replace(
								'{count}',
								String(recommendedInCatalog.length),
							)}
						</Button>
					) : null}
				</div>
			</div>

			<fieldset
				id={listId}
				className="model-picker-list"
				aria-label={t('app.providers.models.listAria', 'Models')}
			>
				{visible.map((model) => (
					<Checkbox
						key={model.id}
						className="model-picker-row"
						label={model.label}
						checked={selected.has(model.id)}
						disabled={disabled}
						onChange={() => toggle(model.id)}
					/>
				))}
				{filtering && filtered.length === 0 ? (
					<p className="field-help model-picker-empty">
						{t('app.providers.models.noMatch', 'No model matches “{query}”.').replace(
							'{query}',
							query.trim(),
						)}
					</p>
				) : null}
			</fieldset>

			{remaining > 0 ? (
				<div className="model-picker-more">
					<span className="field-help tnum">
						{t('app.providers.models.showingOf', 'Showing {shown} of {total}')
							.replace('{shown}', String(visible.length))
							.replace('{total}', String(filtered.length))}
					</span>
					<Button disabled={disabled} onClick={() => setLimit((current) => current + PAGE_SIZE)}>
						{t('app.providers.models.showMore', 'Show {count} more').replace(
							'{count}',
							String(Math.min(PAGE_SIZE, remaining)),
						)}
					</Button>
				</div>
			) : null}
		</div>
	);
}
