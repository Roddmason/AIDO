/**
 * Fetches resolved settings (general and project scopes) and exposes mutate helpers
 * for setting and clearing override values. Mirrors the useTeamActivity fetch pattern:
 * AbortController on enabled, refresh token, no polling (settings are set on demand).
 * @author Rodrigo Mason
 */

import { useCallback, useEffect, useState } from 'react';
import type { ResolvedSetting, SettingsResponse } from '../../api/client';
import { deleteSetting, getSettings, putSetting } from '../../api/client';
import type { JsonValue } from '../../api/generated/openapi';

export type { ResolvedSetting };

export type SettingScope = 'general' | 'project';

export type SettingsState = {
	general: ResolvedSetting[];
	project: ResolvedSetting[];
	loading: boolean;
	error: string;
	refresh: () => void;
	setValue: (
		key: string,
		scope: SettingScope,
		scopeId: string | null,
		value: JsonValue,
	) => Promise<void>;
	clearValue: (key: string, scope: SettingScope, scopeId: string | null) => Promise<void>;
};

/** Loads resolved settings for both scopes while `enabled`; disabled or missing id clears state. */
export function useSettings(
	projectId: string | undefined,
	enabled: boolean,
	token: string,
): SettingsState {
	const [data, setData] = useState<SettingsResponse | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');
	const [reloadToken, setReloadToken] = useState(0);

	const refresh = useCallback(() => setReloadToken((t) => t + 1), []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is an intentional re-fetch trigger (bumped by refresh()), a dependency by design not read inside the effect body.
	useEffect(() => {
		if (!enabled || !projectId) {
			setData(null);
			setError('');
			setLoading(false);
			return;
		}
		let aborted = false;
		const controller = new AbortController();
		setLoading(true);
		getSettings(projectId, controller.signal)
			.then((result) => {
				if (!aborted) {
					setData(result);
					setError('');
				}
			})
			.catch((requestError: unknown) => {
				if (aborted || controller.signal.aborted) return;
				setError(requestError instanceof Error ? requestError.message : 'settings_unavailable');
			})
			.finally(() => {
				if (!aborted) setLoading(false);
			});
		return () => {
			aborted = true;
			controller.abort();
		};
	}, [projectId, enabled, reloadToken]);

	const setValue = useCallback(
		async (key: string, scope: SettingScope, scopeId: string | null, value: JsonValue) => {
			await putSetting(key, { scope, scopeId: scopeId ?? undefined, value }, token);
			refresh();
		},
		[token, refresh],
	);

	const clearValue = useCallback(
		async (key: string, scope: SettingScope, scopeId: string | null) => {
			const params: { scope: string; scopeId?: string } = { scope };
			if (scopeId !== null) params.scopeId = scopeId;
			await deleteSetting(key, params, token);
			refresh();
		},
		[token, refresh],
	);

	return {
		general: data?.general ?? [],
		project: data?.project ?? [],
		loading,
		error,
		refresh,
		setValue,
		clearValue,
	};
}
