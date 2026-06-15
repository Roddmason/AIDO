/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
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

function optionalWithTimeout<T>(promise: Promise<T>, timeoutMs = 4000): Promise<T | null> {
	return new Promise((resolve) => {
		const timeoutId = window.setTimeout(() => resolve(null), timeoutMs);
		promise
			.then((value) => {
				window.clearTimeout(timeoutId);
				resolve(value);
			})
			.catch(() => {
				window.clearTimeout(timeoutId);
				resolve(null);
			});
	});
}

export function useControlPlane() {
	const [state, setState] = useState(initialState);
	const mountedRef = useRef(false);
	const controllersRef = useRef<Set<AbortController>>(new Set());

	const refresh = useCallback(async (silent = false) => {
		const controller = new AbortController();
		controllersRef.current.add(controller);
		if (!silent) setState((current) => ({ ...current, loading: true, error: '' }));
		try {
			const [handshake, overview] = await Promise.all([
				getHandshake(controller.signal),
				getOverview(controller.signal),
			]);
			const [retrievalStatus, runtimeProviders] = await Promise.all([
				optionalWithTimeout(getRetrievalStatus(controller.signal)),
				optionalWithTimeout(getRuntimeProviders(controller.signal)),
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
			if (controller.signal.aborted) return;
			if (!mountedRef.current) return;
			const message = error instanceof Error ? error.message : 'Unable to load control plane state.';
			setState((current) => ({
				...current,
				loading: false,
				error: silent && current.overview ? '' : message,
			}));
		} finally {
			controllersRef.current.delete(controller);
		}
	}, []);

	useEffect(() => {
		mountedRef.current = true;
		void refresh();
		const interval = window.setInterval(() => void refresh(true), 5000);
		return () => {
			mountedRef.current = false;
			window.clearInterval(interval);
			for (const controller of controllersRef.current) {
				controller.abort();
			}
			controllersRef.current.clear();
		};
	}, [refresh]);

	const mutate = useCallback(
		async <T,>(operation: (token: string) => Promise<T>, options: { awaitRefresh?: boolean } = {}) => {
			setState((current) => ({ ...current, busy: true, error: '' }));
			try {
				const result = await operation(state.token);
				const refreshPromise = refresh(true);
				if (options.awaitRefresh === false) {
					void refreshPromise.catch(() => undefined).finally(() => setState((current) => ({ ...current, busy: false })));
					return result;
				}
				await refreshPromise;
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
