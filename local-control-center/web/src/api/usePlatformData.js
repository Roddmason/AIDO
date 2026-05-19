import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { getHandshake, getLegacyState, getOverview, getRetrievalStatus } from './platform-api.js';

const INITIAL_STATE = {
	handshake: null,
	overview: null,
	legacyState: null,
	retrievalStatus: null,
	loading: true,
	error: '',
	connected: false,
	lastUpdatedAt: null,
	busy: false,
};

export function usePlatformData() {
	const [state, setState] = useState(INITIAL_STATE);
	const mountedRef = useRef(false);

	const refresh = useCallback(async ({ silent = false } = {}) => {
		const controller = new AbortController();
		if (!silent) {
			setState((current) => ({ ...current, loading: true, error: '' }));
		}
		try {
			const [handshake, overview, legacyState, retrievalStatus] = await Promise.all([
				getHandshake(controller.signal),
				getOverview(controller.signal),
				getLegacyState(controller.signal),
				getRetrievalStatus(controller.signal),
			]);
			if (!mountedRef.current) return;
			setState((current) => ({
				...current,
				handshake,
				overview,
				legacyState,
				retrievalStatus,
				loading: false,
				error: '',
				lastUpdatedAt: new Date().toISOString(),
			}));
		} catch (error) {
			if (!mountedRef.current) return;
			setState((current) => ({
				...current,
				loading: false,
				error: error.message || 'Unable to load Local Control Center state.',
			}));
		}
		return () => controller.abort();
	}, []);

	useEffect(() => {
		mountedRef.current = true;
		refresh();

		let source;
		try {
			source = new EventSource('/api/v1/events');
			const onSnapshot = (event) => {
				try {
					const overview = JSON.parse(event.data);
					setState((current) => ({
						...current,
						overview,
						connected: true,
						lastUpdatedAt: new Date().toISOString(),
					}));
				} catch (error) {
					setState((current) => ({ ...current, error: error.message }));
				}
			};
			source.addEventListener('snapshot', onSnapshot);
			source.onopen = () => setState((current) => ({ ...current, connected: true }));
			source.onerror = () => setState((current) => ({ ...current, connected: false }));
		} catch {
			setState((current) => ({ ...current, connected: false }));
		}

		const interval = window.setInterval(() => refresh({ silent: true }), 5000);
		return () => {
			mountedRef.current = false;
			window.clearInterval(interval);
			if (source) source.close();
		};
	}, [refresh]);

	const mutate = useCallback(
		async (operation) => {
			const token = state.handshake?.token;
			setState((current) => ({ ...current, busy: true, error: '' }));
			try {
				const result = await operation(token);
				await refresh({ silent: true });
				setState((current) => ({ ...current, busy: false }));
				return result;
			} catch (error) {
				setState((current) => ({
					...current,
					busy: false,
					error: error.message || 'Operation failed.',
				}));
				throw error;
			}
		},
		[refresh, state.handshake?.token],
	);

	return useMemo(() => ({ ...state, refresh, mutate }), [state, refresh, mutate]);
}
