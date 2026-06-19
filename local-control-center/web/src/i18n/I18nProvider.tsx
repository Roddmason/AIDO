/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */

import type { ReactNode } from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { I18nCatalogResponse, I18nLanguageRecord } from '../api/client';
import { getI18nCatalog } from '../api/client';

const LANGUAGE_STORAGE_KEY = 'aido:language';

type I18nContextValue = {
	catalog: I18nCatalogResponse | null;
	language: string;
	languages: I18nLanguageRecord[];
	error: string;
	loading: boolean;
	reloadCatalog: () => Promise<I18nCatalogResponse | null>;
	setCatalog: (catalog: I18nCatalogResponse) => void;
	setLanguage: (language: string) => void;
	t: (key: string, fallback?: string) => string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

function readStoredLanguage() {
	try {
		return window.localStorage.getItem(LANGUAGE_STORAGE_KEY) ?? 'en';
	} catch {
		return 'en';
	}
}

function persistLanguage(language: string) {
	try {
		window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
	} catch {
		// localStorage is optional in restricted browser contexts.
	}
}

export function I18nProvider({ children }: { children: ReactNode }) {
	const [catalog, setCatalogState] = useState<I18nCatalogResponse | null>(null);
	const [language, setLanguageState] = useState(readStoredLanguage);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState('');

	const setCatalog = useCallback((nextCatalog: I18nCatalogResponse) => {
		setCatalogState(nextCatalog);
	}, []);

	const setLanguage = useCallback((nextLanguage: string) => {
		const normalized = nextLanguage.trim().toLowerCase() || 'en';
		setLanguageState(normalized);
		persistLanguage(normalized);
	}, []);

	const reloadCatalog = useCallback(async () => {
		setLoading(true);
		setError('');
		try {
			const nextCatalog = await getI18nCatalog();
			setCatalogState(nextCatalog);
			const enabled = nextCatalog.languages.filter((item) => item.enabled).map((item) => item.code);
			if (!enabled.includes(language)) {
				setLanguage(nextCatalog.defaultLanguage);
			}
			return nextCatalog;
		} catch (requestError) {
			setError(
				requestError instanceof Error
					? requestError.message
					: 'Translation catalog could not be loaded.',
			);
			return null;
		} finally {
			setLoading(false);
		}
	}, [language, setLanguage]);

	useEffect(() => {
		void reloadCatalog();
	}, [reloadCatalog]);

	useEffect(() => {
		document.documentElement.lang = language;
	}, [language]);

	const value = useMemo<I18nContextValue>(
		() => ({
			catalog,
			language,
			languages: catalog?.languages.filter((item) => item.enabled) ?? [],
			error,
			loading,
			reloadCatalog,
			setCatalog,
			setLanguage,
			t: (key, fallback = key) =>
				catalog?.translations[key]?.[language] ??
				catalog?.translations[key]?.[catalog.defaultLanguage] ??
				fallback,
		}),
		[catalog, error, language, loading, reloadCatalog, setCatalog, setLanguage],
	);

	return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
	const context = useContext(I18nContext);
	if (!context) {
		throw new Error('useI18n must be used inside I18nProvider.');
	}
	return context;
}
