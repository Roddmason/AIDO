/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */

import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { EmptyState } from '../components/primitives';
import { useI18n } from '../i18n/I18nProvider';
import type { CommandAction, CommandGroupId } from './commandActions';

const GROUP_ORDER: CommandGroupId[] = ['navigate', 'actions', 'runtime'];
const GROUP_LABELS: Record<CommandGroupId, { labelKey: string; label: string }> = {
	navigate: { labelKey: 'app.commandPalette.groupNavigate', label: 'Navigate' },
	actions: { labelKey: 'app.commandPalette.groupActions', label: 'Actions' },
	runtime: { labelKey: 'app.commandPalette.groupRuntime', label: 'Runtime' },
};

const LISTBOX_ID = 'command-palette-listbox';
const optionId = (id: string) => `command-palette-option-${id}`;
const groupHeaderId = (group: CommandGroupId) => `command-palette-group-${group}`;

function matches(action: CommandAction, query: string): boolean {
	if (!query) return true;
	const haystack = [action.label, action.hint, ...(action.keywords ?? [])].join(' ').toLowerCase();
	return haystack.includes(query);
}

/**
 * IDE-style quick-action center. Opens centered near the top of the viewport,
 * filters by text, navigates with the keyboard (↑/↓/↵) skipping disabled
 * actions, and shows the reason an action is unavailable.
 *
 * Open/close (Ctrl/Cmd+K) and Escape are owned by the App-level capture
 * listener; this component never registers global key handlers.
 */
export function CommandPalette({
	open,
	onClose,
	actions,
}: {
	open: boolean;
	onClose: () => void;
	actions: CommandAction[];
}) {
	const { t } = useI18n();
	const [query, setQuery] = useState('');
	const [activeIndex, setActiveIndex] = useState(0);
	const inputRef = useRef<HTMLInputElement>(null);
	const listRef = useRef<HTMLDivElement>(null);
	const previouslyFocused = useRef<HTMLElement | null>(null);

	const results = useMemo(() => {
		const q = query.trim().toLowerCase();
		const matched = actions.filter((action) => matches(action, q));
		return GROUP_ORDER.flatMap((group) => matched.filter((action) => action.group === group));
	}, [actions, query]);

	const enabledResults = useMemo(() => results.filter((action) => !action.disabled), [results]);
	const activeAction = enabledResults[activeIndex] ?? null;

	const groups = useMemo(
		() =>
			GROUP_ORDER.map((group) => ({
				group,
				label: t(GROUP_LABELS[group].labelKey, GROUP_LABELS[group].label),
				items: results.filter((action) => action.group === group),
			})).filter((entry) => entry.items.length > 0),
		[results, t],
	);

	// Reset the highlight whenever the filter changes or the palette reopens.
	useEffect(() => {
		setActiveIndex(0);
	}, [query, open]);

	// Clear the filter once closed so the next open starts fresh.
	useEffect(() => {
		if (!open) setQuery('');
	}, [open]);

	// Autofocus the input on open; restore focus to the trigger on close.
	useLayoutEffect(() => {
		if (!open) return undefined;
		previouslyFocused.current = (document.activeElement as HTMLElement | null) ?? null;
		inputRef.current?.focus();
		return () => {
			const previous = previouslyFocused.current;
			if (previous && previous.isConnected) previous.focus();
		};
	}, [open]);

	// Keep the highlighted action scrolled into view.
	useEffect(() => {
		if (!open) return;
		listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
	}, [activeIndex, open, results.length]);

	if (!open) return null;

	const runAction = (action: CommandAction) => {
		if (action.disabled) return;
		action.run();
	};

	const onInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
		const count = enabledResults.length;
		switch (event.key) {
			case 'ArrowDown':
				event.preventDefault();
				if (count) setActiveIndex((index) => (index + 1) % count);
				break;
			case 'ArrowUp':
				event.preventDefault();
				if (count) setActiveIndex((index) => (index - 1 + count) % count);
				break;
			case 'Home':
				event.preventDefault();
				setActiveIndex(0);
				break;
			case 'End':
				event.preventDefault();
				if (count) setActiveIndex(count - 1);
				break;
			case 'Enter': {
				event.preventDefault();
				if (activeAction) runAction(activeAction);
				break;
			}
			default:
				break;
		}
	};

	// Minimal focus trap: keep keyboard focus on the search input.
	const onPanelKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
		if (event.key === 'Tab') {
			event.preventDefault();
			inputRef.current?.focus();
		}
	};

	const resultCount = enabledResults.length;

	return (
		<div className="modal-layer command-palette-layer" role="presentation">
			<button
				className="drawer-scrim"
				type="button"
				tabIndex={-1}
				aria-label={t('app.commandPalette.close', 'Close command palette')}
				onClick={onClose}
			/>
			<section
				className="command-palette panel-enter"
				role="dialog"
				aria-modal="true"
				aria-label={t('app.commandPalette.title', 'Command palette')}
				onKeyDown={onPanelKeyDown}
			>
				<input
					ref={inputRef}
					className="input command-palette-input"
					type="text"
					role="combobox"
					aria-expanded="true"
					aria-controls={LISTBOX_ID}
					aria-activedescendant={activeAction ? optionId(activeAction.id) : undefined}
					aria-autocomplete="list"
					aria-label={t('app.commandPalette.filter', 'Filter commands')}
					placeholder={t('app.commandPalette.placeholder', 'Type a command or search…')}
					value={query}
					onChange={(event) => setQuery(event.target.value)}
					onKeyDown={onInputKeyDown}
				/>

				<div
					ref={listRef}
					id={LISTBOX_ID}
					className="command-palette-results"
					role="listbox"
					aria-label={t('app.commandPalette.title', 'Command palette')}
				>
					{results.length === 0 ? (
						<EmptyState
							title={t('ui.static.no.commands.c0f14586', 'No commands')}
							body={t(
								'app.commandPalette.empty.body',
								'Try open, runtime, evidence, approvals or settings.',
							)}
						/>
					) : (
						groups.map((entry) => (
							<div
								key={entry.group}
								className="command-palette-group"
								role="group"
								aria-labelledby={groupHeaderId(entry.group)}
							>
								<div id={groupHeaderId(entry.group)} className="command-group-header">
									{entry.label}
								</div>
								{entry.items.map((action) => {
									const Icon = action.icon;
									const disabled = Boolean(action.disabled);
									const isActive = activeAction?.id === action.id;
									const reason =
										disabled && action.disabledReason ? action.disabledReason : action.hint;
									return (
										<button
											key={action.id}
											id={optionId(action.id)}
											type="button"
											role="option"
											aria-selected={isActive}
											aria-disabled={disabled || undefined}
											aria-keyshortcuts={action.shortcut?.join('+')}
											data-active={isActive ? 'true' : undefined}
											tabIndex={-1}
											className="command-item"
											onClick={() => runAction(action)}
											onMouseMove={() => {
												if (disabled) return;
												const index = enabledResults.findIndex((item) => item.id === action.id);
												if (index >= 0 && index !== activeIndex) setActiveIndex(index);
											}}
										>
											<span className="command-item-row">
												{Icon ? (
													<Icon aria-hidden="true" size={16} className="command-item-icon" />
												) : null}
												<span className="command-item-label">{action.label}</span>
												{action.badge ? (
													<span className="command-item-badge">{action.badge}</span>
												) : null}
												{action.shortcut ? (
													<span className="command-item-shortcut" aria-hidden="true">
														{action.shortcut.map((token) => (
															<kbd key={token} className="command-kbd">
																{token}
															</kbd>
														))}
													</span>
												) : null}
											</span>
											<small>{reason}</small>
										</button>
									);
								})}
							</div>
						))
					)}
				</div>

				<div className="command-palette-footer">
					<span className="sr-only" aria-live="polite">
						{resultCount === 1
							? `${resultCount} ${t('app.commandPalette.commandSingular', 'command')}`
							: `${resultCount} ${t('app.commandPalette.commandPlural', 'commands')}`}
					</span>
					<span className="command-palette-hints" aria-hidden="true">
						<kbd className="command-kbd">↑</kbd>
						<kbd className="command-kbd">↓</kbd>
						<span>{t('app.commandPalette.hintNavigate', 'navigate')}</span>
						<kbd className="command-kbd">↵</kbd>
						<span>{t('app.commandPalette.hintRun', 'run')}</span>
						<kbd className="command-kbd">esc</kbd>
						<span>{t('app.commandPalette.hintDismiss', 'dismiss')}</span>
					</span>
				</div>
			</section>
		</div>
	);
}
