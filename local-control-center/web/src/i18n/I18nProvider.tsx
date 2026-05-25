import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { getI18nCatalog } from '../api/client';
import type { I18nCatalogResponse, I18nLanguageRecord } from '../api/client';

const LANGUAGE_STORAGE_KEY = 'aido:language';
const LOCALIZED_ATTRIBUTES = ['aria-label', 'aria-description', 'placeholder', 'title', 'data-label'];
const TEXT_LOCALIZATION_BLOCKLIST = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'INPUT', 'CODE', 'PRE', 'KBD', 'SAMP']);

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

function buildTextMap(catalog: I18nCatalogResponse, language: string) {
	const map = new Map<string, string>();
	for (const values of Object.values(catalog.translations)) {
		const target = values[language] ?? values[catalog.defaultLanguage] ?? '';
		if (!target) continue;
		for (const source of Object.values(values)) {
			if (source && source !== target) map.set(source, target);
		}
	}
	return map;
}

function translateExact(value: string, textMap: Map<string, string>) {
	const trimmed = value.trim();
	if (!trimmed) return value;
	const translated = textMap.get(trimmed);
	if (!translated) return value;
	const leading = value.match(/^\s*/)?.[0] ?? '';
	const trailing = value.match(/\s*$/)?.[0] ?? '';
	return `${leading}${translated}${trailing}`;
}

function localizeNode(root: ParentNode, textMap: Map<string, string>) {
	const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
	const textNodes: Text[] = [];
	while (walker.nextNode()) {
		const node = walker.currentNode as Text;
		const parent = node.parentElement;
		if (!parent || TEXT_LOCALIZATION_BLOCKLIST.has(parent.tagName)) continue;
		textNodes.push(node);
	}
	for (const node of textNodes) {
		const translated = translateExact(node.nodeValue ?? '', textMap);
		if (translated !== node.nodeValue) node.nodeValue = translated;
	}
	const elements = root instanceof Element ? [root, ...Array.from(root.querySelectorAll('*'))] : Array.from(root.querySelectorAll('*'));
	for (const element of elements) {
		for (const attribute of LOCALIZED_ATTRIBUTES) {
			const value = element.getAttribute(attribute);
			if (!value) continue;
			const translated = translateExact(value, textMap);
			if (translated !== value) element.setAttribute(attribute, translated);
		}
	}
}

function useRuntimeLocalizer(catalog: I18nCatalogResponse | null, language: string) {
	useEffect(() => {
		if (!catalog) return;
		const textMap = buildTextMap(catalog, language);
		if (!textMap.size) return;
		localizeNode(document.body, textMap);
		const observer = new MutationObserver((mutations) => {
			for (const mutation of mutations) {
				if (mutation.type === 'characterData') {
					const node = mutation.target as Text;
					const translated = translateExact(node.nodeValue ?? '', textMap);
					if (translated !== node.nodeValue) node.nodeValue = translated;
				}
				if (mutation.type === 'attributes' && mutation.target instanceof Element) {
					localizeNode(mutation.target, textMap);
				}
				for (const node of mutation.addedNodes) {
					if (node instanceof Element) localizeNode(node, textMap);
					if (node instanceof Text) {
						const translated = translateExact(node.nodeValue ?? '', textMap);
						if (translated !== node.nodeValue) node.nodeValue = translated;
					}
				}
			}
		});
		observer.observe(document.body, {
			attributes: true,
			attributeFilter: LOCALIZED_ATTRIBUTES,
			childList: true,
			characterData: true,
			subtree: true,
		});
		return () => observer.disconnect();
	}, [catalog, language]);
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
			setError(requestError instanceof Error ? requestError.message : 'Translation catalog could not be loaded.');
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

	useRuntimeLocalizer(catalog, language);

	const value = useMemo<I18nContextValue>(() => ({
		catalog,
		language,
		languages: catalog?.languages.filter((item) => item.enabled) ?? [],
		error,
		loading,
		reloadCatalog,
		setCatalog,
		setLanguage,
		t: (key, fallback = key) => catalog?.translations[key]?.[language] ?? catalog?.translations[key]?.[catalog.defaultLanguage] ?? fallback,
	}), [catalog, error, language, loading, reloadCatalog, setCatalog, setLanguage]);

	return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
	const context = useContext(I18nContext);
	if (!context) {
		throw new Error('useI18n must be used inside I18nProvider.');
	}
	return context;
}
