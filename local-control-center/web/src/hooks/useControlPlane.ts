/**
 * Owns the control-plane session: handshake token, polled state, and mutations.
 *
 * Performs the authenticated handshake once (the token is static per backend process),
 * polls overview/status every 5s, and exposes a `mutate` runner that injects the write
 * token and refreshes afterwards. A write rejected for a stale token clears the cached
 * token so the next tick re-negotiates the handshake (backend restart). Secondary
 * reads are wrapped in a timeout so a slow optional endpoint cannot stall the page.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
	getHandshake,
	getOverview,
	getRetrievalStatus,
	getRuntimeProviderConfiguration,
	getRuntimeProviders,
} from '../api/client';
import type {
	Overview,
	RetrievalStatus,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../api/types';
import { useI18n } from '../i18n/I18nProvider';

type ControlPlaneState = {
	token: string;
	overview: Overview | null;
	retrievalStatus: RetrievalStatus | null;
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
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
	runtimeProviderConfiguration: null,
	loading: true,
	error: '',
	connected: false,
	lastUpdatedAt: '',
	busy: false,
};

const OPTIONAL_ENDPOINT_TIMEOUT_MS = 4000;
const RUNTIME_ENDPOINT_TIMEOUT_MS = 15000;

/** Resolves to `null` instead of rejecting/hanging if the promise fails or exceeds the timeout. */
function optionalWithTimeout<T>(
	promise: Promise<T>,
	timeoutMs = OPTIONAL_ENDPOINT_TIMEOUT_MS,
): Promise<T | null> {
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
	const tokenRef = useRef('');
	const controllersRef = useRef<Set<AbortController>>(new Set());
	const { t } = useI18n();
	const tRef = useRef(t);
	tRef.current = t;

	const refresh = useCallback(async (silent = false) => {
		const controller = new AbortController();
		let secondaryStarted = false;
		controllersRef.current.add(controller);
		if (!silent) setState((current) => ({ ...current, loading: true, error: '' }));
		const refreshSecondaryState = async () => {
			try {
				const [retrievalStatus, runtimeProviders, runtimeProviderConfigurationResponse] =
					await Promise.all([
						optionalWithTimeout(getRetrievalStatus(controller.signal)),
						optionalWithTimeout(
							getRuntimeProviders(controller.signal),
							RUNTIME_ENDPOINT_TIMEOUT_MS,
						),
						optionalWithTimeout(
							getRuntimeProviderConfiguration(controller.signal),
							RUNTIME_ENDPOINT_TIMEOUT_MS,
						),
					]);
				if (!mountedRef.current || controller.signal.aborted) return;
				setState((current) => ({
					...current,
					retrievalStatus,
					runtimeProviders,
					runtimeProviderConfiguration:
						runtimeProviderConfigurationResponse?.providers ?? current.runtimeProviderConfiguration,
					lastUpdatedAt: new Date().toISOString(),
				}));
			} finally {
				controllersRef.current.delete(controller);
			}
		};
		try {
			const [handshake, overview] = await Promise.all([
				tokenRef.current
					? Promise.resolve({ token: tokenRef.current })
					: getHandshake(controller.signal),
				getOverview(controller.signal),
			]);
			if (!mountedRef.current) return;
			tokenRef.current = handshake.token;
			setState((current) => ({
				...current,
				token: handshake.token,
				overview,
				loading: false,
				error: '',
				connected: true,
				lastUpdatedAt: new Date().toISOString(),
			}));
			secondaryStarted = true;
			void refreshSecondaryState();
		} catch (error) {
			if (controller.signal.aborted) return;
			if (!mountedRef.current) return;
			const message =
				error instanceof Error
					? error.message
					: tRef.current(
							'app.controlPlane.error.loadFailed',
							'Unable to load control plane state.',
						);
			setState((current) => ({
				...current,
				loading: false,
				connected: false,
				error: silent && current.overview ? '' : message,
			}));
		} finally {
			if (!secondaryStarted) controllersRef.current.delete(controller);
		}
	}, []);

	useEffect(() => {
		mountedRef.current = true;
		void refresh();
		// Pestaña oculta: no hay nadie mirando el snapshot, asi que el tick no gasta red ni
		// lock del backend; al volver a visible se refresca de inmediato para no mostrar
		// datos de hace minutos.
		const interval = window.setInterval(() => {
			if (document.visibilityState === 'hidden') return;
			void refresh(true);
		}, 5000);
		const onVisibilityChange = () => {
			if (document.visibilityState === 'visible') void refresh(true);
		};
		document.addEventListener('visibilitychange', onVisibilityChange);
		return () => {
			mountedRef.current = false;
			window.clearInterval(interval);
			document.removeEventListener('visibilitychange', onVisibilityChange);
			for (const controller of controllersRef.current) {
				controller.abort();
			}
			controllersRef.current.clear();
		};
	}, [refresh]);

	const mutate = useCallback(
		async <T>(
			operation: (token: string) => Promise<T>,
			options: { awaitRefresh?: boolean } = {},
		) => {
			if (!state.token) {
				const message = tRef.current(
					'app.controlPlane.error.connecting',
					'Control plane is still connecting; retry once the session token is ready.',
				);
				setState((current) => ({ ...current, error: message }));
				throw new Error(message);
			}
			setState((current) => ({ ...current, busy: true, error: '' }));
			try {
				const result = await operation(state.token);
				const refreshPromise = refresh(true);
				if (options.awaitRefresh === false) {
					void refreshPromise
						.catch(() => undefined)
						.finally(() => setState((current) => ({ ...current, busy: false })));
					return result;
				}
				await refreshPromise;
				setState((current) => ({ ...current, busy: false }));
				return result;
			} catch (error) {
				if ((error as { name?: string } | null)?.name === 'AbortError') {
					setState((current) => ({ ...current, busy: false }));
					throw error;
				}
				if (error instanceof Error && error.message.includes('loopback write token')) {
					tokenRef.current = '';
					setState((current) => ({ ...current, token: '' }));
				}
				setState((current) => ({
					...current,
					busy: false,
					error:
						error instanceof Error
							? error.message
							: tRef.current('app.controlPlane.error.operationFailed', 'Operation failed.'),
				}));
				throw error;
			}
		},
		[refresh, state.token],
	);

	return useMemo(() => ({ ...state, refresh, mutate }), [state, refresh, mutate]);
}
