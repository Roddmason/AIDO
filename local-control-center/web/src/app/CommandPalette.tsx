/**
 * IDE command palette: fuzzy-filtered, keyboard-driven quick-action launcher.
 *
 * Groups actions by intent (navigate/actions/runtime) and exposes them as an ARIA
 * combobox+listbox. Open/close and Escape are owned by the App shortcut layer.
 * @author Rodrigo Mason
 */

import { m } from 'motion/react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { EmptyState } from '../components/ui';
import { useI18n } from '../i18n/I18nProvider';
import { cardTransition, dialogTransition, listStagger } from '../motion/variants';
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

function runAction(action: CommandAction): void {
	if (action.disabled) return;
	action.run();
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
	if (!open) return null;
	return <CommandPaletteContent onClose={onClose} actions={actions} />;
}

/** Mounted only while the palette is open so query/highlight state resets by unmounting. */
function CommandPaletteContent({
	onClose,
	actions,
}: {
	onClose: () => void;
	actions: CommandAction[];
}) {
	const { t } = useI18n();
	const [query, setQuery] = useState('');
	const [activeIndex, setActiveIndex] = useState(0);
	const inputRef = useRef<HTMLInputElement>(null);
	const listRef = useRef<HTMLDivElement>(null);

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

	useLayoutEffect(() => {
		const previouslyFocused = (document.activeElement as HTMLElement | null) ?? null;
		inputRef.current?.focus();
		return () => {
			if (previouslyFocused?.isConnected) previouslyFocused.focus();
		};
	}, []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: activeIndex/results.length are intentional triggers — re-scroll the active option into view when the selection moves or the result set changes.
	useEffect(() => {
		listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
	}, [activeIndex, results.length]);

	const onQueryChange = (value: string) => {
		if (value === query) return;
		setQuery(value);
		setActiveIndex(0);
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

	const onPanelKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
		if (event.key === 'Tab') {
			event.preventDefault();
			inputRef.current?.focus();
		}
	};

	const resultCount = enabledResults.length;

	return (
		<div className="modal-layer command-palette-layer" role="presentation">
			<m.button
				className="drawer-scrim"
				type="button"
				tabIndex={-1}
				aria-label={t('app.commandPalette.close', 'Close command palette')}
				onClick={onClose}
				style={{ backdropFilter: 'blur(2px)' }}
				initial={{ opacity: 0 }}
				animate={{ opacity: 1, transition: { duration: 0.16 } }}
			/>
			<m.section
				className="command-palette"
				role="dialog"
				aria-modal="true"
				aria-label={t('app.commandPalette.title', 'Command palette')}
				onKeyDown={onPanelKeyDown}
				variants={dialogTransition}
				initial="initial"
				animate="animate"
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
					onChange={(event) => onQueryChange(event.target.value)}
					onKeyDown={onInputKeyDown}
				/>

				<m.div
					ref={listRef}
					id={LISTBOX_ID}
					className="command-palette-results"
					role="listbox"
					aria-label={t('app.commandPalette.title', 'Command palette')}
					variants={listStagger}
					initial="initial"
					animate="animate"
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
							// biome-ignore lint/a11y/useSemanticElements: role="group" is the WAI-ARIA listbox-grouping pattern; <fieldset> carries form-control semantics and UA chrome that breaks the grid layout.
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
										<m.button
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
											variants={cardTransition}
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
										</m.button>
									);
								})}
							</div>
						))
					)}
				</m.div>

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
			</m.section>
		</div>
	);
}
