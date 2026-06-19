/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { Languages, Plus, Save, Undo2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { I18nCatalogResponse } from '../api/client';
import { updateI18nCatalog } from '../api/client';
import { EmptyState, Surface } from '../components/primitives';
import { useI18n } from './I18nProvider';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

function cloneCatalog(catalog: I18nCatalogResponse): I18nCatalogResponse {
	return {
		defaultLanguage: catalog.defaultLanguage,
		languages: catalog.languages.map((language) => ({ ...language })),
		translations: Object.fromEntries(
			Object.entries(catalog.translations).map(([key, values]) => [key, { ...values }]),
		),
	};
}

function normalizeLanguageCode(value: string) {
	return value.trim().toLowerCase();
}

export function TranslationMaintainer({ mutate }: { mutate: Mutate }) {
	const { catalog, error: loadError, loading, reloadCatalog, setCatalog, t } = useI18n();
	const [draft, setDraft] = useState<I18nCatalogResponse | null>(
		catalog ? cloneCatalog(catalog) : null,
	);
	const [filter, setFilter] = useState('');
	const [newLanguageCode, setNewLanguageCode] = useState('');
	const [newLanguageName, setNewLanguageName] = useState('');
	const [newLanguageNativeName, setNewLanguageNativeName] = useState('');
	const [newKey, setNewKey] = useState('');
	const [message, setMessage] = useState('');
	const [error, setError] = useState('');
	const [busy, setBusy] = useState(false);

	useEffect(() => {
		if (catalog) setDraft(cloneCatalog(catalog));
	}, [catalog]);

	const languageCodes =
		draft?.languages.filter((language) => language.enabled).map((language) => language.code) ?? [];
	const visibleEntries = useMemo(() => {
		if (!draft) return [];
		const query = filter.trim().toLowerCase();
		return Object.entries(draft.translations)
			.filter(([key, values]) => {
				if (!query) return true;
				return (
					key.toLowerCase().includes(query) ||
					Object.values(values).some((value) => value.toLowerCase().includes(query))
				);
			})
			.sort(([left], [right]) => left.localeCompare(right));
	}, [draft, filter]);

	const updateValue = (key: string, languageCode: string, value: string) => {
		setDraft((current) => {
			if (!current) return current;
			return {
				...current,
				translations: {
					...current.translations,
					[key]: {
						...current.translations[key],
						[languageCode]: value,
					},
				},
			};
		});
	};

	const addLanguage = () => {
		if (!draft) return;
		setMessage('');
		setError('');
		const code = normalizeLanguageCode(newLanguageCode);
		const name = newLanguageName.trim();
		const nativeName = newLanguageNativeName.trim() || name;
		if (!code) {
			setError(t('i18n.maintainer.codeRequired', 'Language code is required.'));
			return;
		}
		if (!name) {
			setError(t('i18n.maintainer.nameRequired', 'Language name is required.'));
			return;
		}
		if (draft.languages.some((language) => language.code === code)) {
			setError(t('i18n.maintainer.codeExists', 'Language code already exists.'));
			return;
		}
		const translations = Object.fromEntries(
			Object.entries(draft.translations).map(([key, values]) => [
				key,
				{
					...values,
					[code]: values[draft.defaultLanguage] ?? values.en ?? key,
				},
			]),
		);
		setDraft({
			...draft,
			languages: [...draft.languages, { code, name, nativeName, enabled: true }],
			translations,
		});
		setNewLanguageCode('');
		setNewLanguageName('');
		setNewLanguageNativeName('');
	};

	const addTranslationKey = () => {
		if (!draft) return;
		setMessage('');
		setError('');
		const key = newKey.trim();
		if (!key || draft.translations[key]) return;
		setDraft({
			...draft,
			translations: {
				...draft.translations,
				[key]: Object.fromEntries(languageCodes.map((code) => [code, key])),
			},
		});
		setNewKey('');
	};

	const save = async () => {
		if (!draft) return;
		setBusy(true);
		setMessage('');
		setError('');
		try {
			const saved = await mutate((token) => updateI18nCatalog(token, draft));
			setCatalog(saved);
			setMessage(t('i18n.maintainer.saved', 'Translation catalog saved.'));
		} catch (requestError) {
			setError(
				requestError instanceof Error
					? requestError.message
					: t('i18n.maintainer.saveFailed', 'Translation catalog could not be saved.'),
			);
		} finally {
			setBusy(false);
		}
	};

	if (loading && !draft) {
		return (
			<EmptyState
				title={t('i18n.maintainer.loadingTitle', 'Loading translations')}
				body={t('i18n.maintainer.loadingBody', 'Fetching the runtime translation catalog.')}
			/>
		);
	}

	if (!draft) {
		return (
			<EmptyState
				title={t('i18n.maintainer.loadFailed', 'Translation catalog could not be loaded.')}
				body={loadError || 'The translation API did not return a catalog.'}
			/>
		);
	}

	return (
		<div className="translation-maintainer">
			<Surface title={t('i18n.maintainer.title', 'Translation maintainer')}>
				<div className="stack">
					<p className="muted">
						{t(
							'i18n.maintainer.summary',
							'Edit copy, add languages, and persist the runtime catalog without rebuilding the dashboard.',
						)}
					</p>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="i18n-language-code">
								{t('i18n.maintainer.languageCode', 'Language code')}
							</label>
							<input
								id="i18n-language-code"
								className="input"
								value={newLanguageCode}
								onChange={(event) => setNewLanguageCode(event.target.value)}
								placeholder="pt-br"
							/>
						</div>
						<div className="field">
							<label htmlFor="i18n-language-name">
								{t('i18n.maintainer.languageName', 'Language name')}
							</label>
							<input
								id="i18n-language-name"
								className="input"
								value={newLanguageName}
								onChange={(event) => setNewLanguageName(event.target.value)}
								placeholder={t('app.i18nMaintainer.languageNamePlaceholder', 'Portuguese')}
							/>
						</div>
						<div className="field">
							<label htmlFor="i18n-language-native">
								{t('i18n.maintainer.nativeName', 'Native name')}
							</label>
							<input
								id="i18n-language-native"
								className="input"
								value={newLanguageNativeName}
								onChange={(event) => setNewLanguageNativeName(event.target.value)}
								placeholder={t('app.i18nMaintainer.nativeNamePlaceholder', 'Portugues')}
							/>
						</div>
						<button className="button settings-action" type="button" onClick={addLanguage}>
							<Plus aria-hidden="true" size={16} />
							{t('i18n.maintainer.addLanguage', 'Add language')}
						</button>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="i18n-new-key">
								{t('i18n.maintainer.translationKey', 'Translation key')}
							</label>
							<input
								id="i18n-new-key"
								className="input"
								value={newKey}
								onChange={(event) => setNewKey(event.target.value)}
								placeholder="feature.section.label"
							/>
						</div>
						<button className="button settings-action" type="button" onClick={addTranslationKey}>
							<Languages aria-hidden="true" size={16} />
							{t('i18n.maintainer.addTranslationKey', 'Add translation key')}
						</button>
					</div>
					<div className="inline">
						<button
							className="button primary"
							type="button"
							onClick={() => void save()}
							disabled={busy}
						>
							<Save aria-hidden="true" size={16} />
							{t('i18n.maintainer.saveCatalog', 'Save translation catalog')}
						</button>
						<button
							className="button"
							type="button"
							onClick={() => {
								setDraft(catalog ? cloneCatalog(catalog) : draft);
								void reloadCatalog();
							}}
							disabled={busy}
						>
							<Undo2 aria-hidden="true" size={16} />
							{t('i18n.maintainer.resetDraft', 'Reset draft')}
						</button>
					</div>
					{message ? (
						<div className="badge" data-tone="ok">
							{message}
						</div>
					) : null}
					{error ? (
						<div className="form-error" role="alert">
							{error}
						</div>
					) : null}
				</div>
			</Surface>
			<Surface>
				<div className="field">
					<label htmlFor="i18n-filter">{t('i18n.maintainer.filter', 'Translation filter')}</label>
					<input
						id="i18n-filter"
						className="input"
						value={filter}
						onChange={(event) => setFilter(event.target.value)}
						placeholder={t('i18n.maintainer.filterPlaceholder', 'Filter by key or text')}
					/>
				</div>
				{visibleEntries.length ? (
					<div className="table-wrap">
						<table className="data-table">
							<thead>
								<tr>
									<th scope="col">{t('i18n.maintainer.translationKey', 'Translation key')}</th>
									{languageCodes.map((code) => (
										<th key={code} scope="col">
											{code.toUpperCase()}
										</th>
									))}
								</tr>
							</thead>
							<tbody>
								{visibleEntries.map(([key, values]) => (
									<tr key={key}>
										<td data-label={t('i18n.maintainer.translationKey', 'Translation key')}>
											<span className="mono">{key}</span>
										</td>
										{languageCodes.map((code) => (
											<td key={code} data-label={code.toUpperCase()}>
												<input
													className="input"
													aria-label={`${key} ${code}`}
													value={values[code] ?? ''}
													onChange={(event) => updateValue(key, code, event.target.value)}
												/>
											</td>
										))}
									</tr>
								))}
							</tbody>
						</table>
					</div>
				) : (
					<EmptyState
						title={t('i18n.maintainer.empty', 'No translation keys match the filter.')}
						body={filter}
					/>
				)}
			</Surface>
		</div>
	);
}
