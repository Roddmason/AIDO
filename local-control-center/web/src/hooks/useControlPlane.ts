import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { getHandshake, getOverview, getRetrievalStatus, getRuntimeProviders } from '../api/client';
import type { Overview, RetrievalStatus, RuntimeProviders } from '../api/types';

type ControlPlaneState = {
	token: string;
	overview: Overview | null;
	retrievalStatus: RetrievalStatus | null;
	runtimeProviders: RuntimeProviders | null;
	loading: boolean;
	error: string;
	connected: boolean;
	lastUpdatedAt: string;
	busy: boolean;
};

const initialState: ControlPlaneState = {
	token: '',
	overview: null,
	retrievalStatus: null,
	runtimeProviders: null,
	loading: true,
	error: '',
	connected: false,
	lastUpdatedAt: '',
	busy: false,
};

export function useControlPlane() {
	const [state, setState] = useState(initialState);
	const mountedRef = useRef(false);

	const refresh = useCallback(async (silent = false) => {
		const controller = new AbortController();
		if (!silent) setState((current) => ({ ...current, loading: true, error: '' }));
		try {
			const [handshake, overview, retrievalStatus, runtimeProviders] = await Promise.all([
				getHandshake(controller.signal),
				getOverview(controller.signal),
				getRetrievalStatus(controller.signal),
				getRuntimeProviders(controller.signal),
			]);
			if (!mountedRef.current) return;
			setState((current) => ({
				...current,
				token: handshake.token,
				overview,
				retrievalStatus,
				runtimeProviders,
				loading: false,
				error: '',
				lastUpdatedAt: new Date().toISOString(),
			}));
		} catch (error) {
			if (!mountedRef.current) return;
			setState((current) => ({
				...current,
				loading: false,
				error: error instanceof Error ? error.message : 'Unable to load control plane state.',
			}));
		}
		return () => controller.abort();
	}, []);

	useEffect(() => {
		mountedRef.current = true;
		void refresh();
		let source: EventSource | null = null;
		try {
			source = new EventSource('/api/v1/events');
			source.addEventListener('snapshot', (event) => {
				const overview = JSON.parse((event as MessageEvent).data) as Overview;
				setState((current) => ({
					...current,
					overview,
					connected: true,
					lastUpdatedAt: new Date().toISOString(),
				}));
			});
			source.onopen = () => setState((current) => ({ ...current, connected: true }));
			source.onerror = () => setState((current) => ({ ...current, connected: false }));
		} catch {
			setState((current) => ({ ...current, connected: false }));
		}
		const interval = window.setInterval(() => void refresh(true), 5000);
		return () => {
			mountedRef.current = false;
			window.clearInterval(interval);
			source?.close();
		};
	}, [refresh]);

	const mutate = useCallback(
		async <T,>(operation: (token: string) => Promise<T>) => {
			setState((current) => ({ ...current, busy: true, error: '' }));
			try {
				const result = await operation(state.token);
				await refresh(true);
				setState((current) => ({ ...current, busy: false }));
				return result;
			} catch (error) {
				setState((current) => ({
					...current,
					busy: false,
					error: error instanceof Error ? error.message : 'Operation failed.',
				}));
				throw error;
			}
		},
		[refresh, state.token],
	);

	return useMemo(() => ({ ...state, refresh, mutate }), [state, refresh, mutate]);
}
