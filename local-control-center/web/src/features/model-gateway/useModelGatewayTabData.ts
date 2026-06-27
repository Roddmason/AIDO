/**
 * Per-tab lazy data loader for the Model Gateway console. Each tab declares the named API
 * resources it needs; `ensure(tab)` fetches only the ones not already cached, under a single
 * AbortController that is aborted when the active tab changes (so a tab switch genuinely cancels
 * the in-flight HTTP requests — the generated client threads the signal into fetch). Shared
 * resources (models, providers) are fetched on cache-miss so a cold deep-link to any tab
 * populates its dependencies, and skipped when already loaded so revisiting a tab never refetches.
 * @author Rodrigo Mason
 */
import {
	type Dispatch,
	type SetStateAction,
	useCallback,
	useEffect,
	useRef,
	useState,
} from 'react';

import {
	getModelGatewayBenchmarkOutcomes,
	getModelGatewayBenchmarks,
	getModelGatewayBudgetRules,
	getModelGatewayCliRuntimes,
	getModelGatewayCliSessions,
	getModelGatewayModels,
	getModelGatewayOverview,
	getModelGatewayProviderLimits,
	getModelGatewayProviders,
	getModelGatewayRolePolicies,
	getModelGatewayRoutingDecisions,
	getModelGatewayRoutingProfiles,
	getModelGatewayUsageLedger,
	getModelGatewayUsageSummary,
	getRuntimeProviderConfiguration,
	getRuntimeProviders,
} from '../../api/client';
import type {
	ModelGatewayBenchmark,
	ModelGatewayBenchmarkOutcome,
	ModelGatewayBudgetRule,
	ModelGatewayCliRuntime,
	ModelGatewayCliSession,
	ModelGatewayModel,
	ModelGatewayOverview,
	ModelGatewayProviderAccount,
	ModelGatewayProviderLimit,
	ModelGatewayRolePolicy,
	ModelGatewayRoutingDecision,
	ModelGatewayRoutingProfile,
	ModelGatewayUsage,
	ModelGatewayUsageSummary,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';

export type ModelGatewayTab =
	| 'providers'
	| 'catalog'
	| 'routing'
	| 'policies'
	| 'budgets'
	| 'usage'
	| 'benchmarks'
	| 'decisions'
	| 'cli';

export const MODEL_GATEWAY_TABS: ModelGatewayTab[] = [
	'providers',
	'catalog',
	'routing',
	'policies',
	'budgets',
	'usage',
	'benchmarks',
	'decisions',
	'cli',
];

/** Data the console renders, accumulated across visited tabs. */
export type ModelGatewayState = {
	overview: ModelGatewayOverview;
	providers: ModelGatewayProviderAccount[];
	models: ModelGatewayModel[];
	routingProfiles: ModelGatewayRoutingProfile[];
	rolePolicies: ModelGatewayRolePolicy[];
	usageLedger: ModelGatewayUsage[];
	usageSummary: ModelGatewayUsageSummary | null;
	routingDecisions: ModelGatewayRoutingDecision[];
	providerLimits: ModelGatewayProviderLimit[];
	budgetRules: ModelGatewayBudgetRule[];
	cliRuntimes: ModelGatewayCliRuntime[];
	cliSessions: ModelGatewayCliSession[];
	benchmarks: ModelGatewayBenchmark[];
	benchmarkOutcomes: ModelGatewayBenchmarkOutcome[];
	runtimeProviderConfiguration: RuntimeProviderConfiguration[];
};

export const emptyGatewayState: ModelGatewayState = {
	overview: {
		activeCliSessions: 0,
		actualCostToday: 0,
		apiProviders: 0,
		cliRuntimes: 0,
		degraded: 0,
		estimatedCostToday: 0,
		healthy: 0,
		localProviders: 0,
		offline: 0,
		pendingModelApprovals: 0,
		providersEnabled: 0,
		providersInCooldown: 0,
		totalTokensToday: 0,
	},
	providers: [],
	models: [],
	routingProfiles: [],
	rolePolicies: [],
	usageLedger: [],
	usageSummary: null,
	routingDecisions: [],
	providerLimits: [],
	budgetRules: [],
	cliRuntimes: [],
	cliSessions: [],
	benchmarks: [],
	benchmarkOutcomes: [],
	runtimeProviderConfiguration: [],
};

export type TabStatus = 'idle' | 'loading' | 'ready' | 'error';
export type TabSlice = { status: TabStatus; error: string };

type SetGateway = Dispatch<SetStateAction<ModelGatewayState>>;
type SetRuntimeProviders = Dispatch<SetStateAction<RuntimeProviders | null>>;
type Translate = (key: string, fallback?: string) => string;

/** A named, independently-cacheable fetch. */
type ResourceKey =
	| 'overview'
	| 'runtimeConfig'
	| 'runtimeProviders'
	| 'providers'
	| 'models'
	| 'routingProfiles'
	| 'rolePolicies'
	| 'budgetRules'
	| 'providerLimits'
	| 'usageLedger'
	| 'usageSummary'
	| 'benchmarks'
	| 'benchmarkOutcomes'
	| 'routingDecisions'
	| 'cliRuntimes'
	| 'cliSessions';

const OVERVIEW_RESOURCES: ResourceKey[] = ['overview', 'runtimeConfig'];
const TAB_RESOURCES: Record<ModelGatewayTab, ResourceKey[]> = {
	providers: ['providers', 'runtimeProviders', 'runtimeConfig'],
	catalog: ['models'],
	routing: ['routingProfiles', 'models'],
	policies: ['rolePolicies', 'models'],
	budgets: ['budgetRules', 'providerLimits'],
	usage: ['usageLedger', 'usageSummary'],
	benchmarks: ['benchmarks', 'benchmarkOutcomes', 'providers', 'models'],
	decisions: ['routingDecisions'],
	cli: ['cliRuntimes', 'cliSessions'],
};

export type ModelGatewayTabData = {
	slice: (tab: ModelGatewayTab | 'overview') => TabSlice;
	ensure: (tab: ModelGatewayTab | 'overview') => void;
	refresh: (tab: ModelGatewayTab | 'overview') => void;
};

/**
 * Owns per-tab/overview load status and a resource cache. Call `ensure(activeTab)` from an effect
 * keyed on the active tab; the hook aborts the prior tab's requests on switch. `refresh(tab)`
 * forces a re-fetch after a mutation (narrow invalidation instead of reloading every endpoint).
 */
export function useModelGatewayTabData({
	activeTab,
	setGateway,
	setRuntimeProviderState,
	t,
}: {
	activeTab: ModelGatewayTab;
	setGateway: SetGateway;
	setRuntimeProviderState: SetRuntimeProviders;
	t: Translate;
}): ModelGatewayTabData {
	const [slices, setSlices] = useState<Record<string, TabSlice>>({});
	const slicesRef = useRef(slices);
	slicesRef.current = slices;
	const loadedRef = useRef<Set<ResourceKey>>(new Set());
	const controllersRef = useRef<Map<string, AbortController>>(new Map());
	const tRef = useRef(t);
	tRef.current = t;
	const setGatewayRef = useRef(setGateway);
	setGatewayRef.current = setGateway;
	const setRuntimeRef = useRef(setRuntimeProviderState);
	setRuntimeRef.current = setRuntimeProviderState;

	const setSlice = useCallback((group: string, slice: TabSlice) => {
		setSlices((current) => ({ ...current, [group]: slice }));
	}, []);

	const fetchResource = useCallback(async (key: ResourceKey, signal: AbortSignal) => {
		const patch = setGatewayRef.current;
		switch (key) {
			case 'overview': {
				const payload = await getModelGatewayOverview(signal);
				patch((c) => ({ ...c, overview: payload.overview }));
				return;
			}
			case 'runtimeConfig': {
				const payload = await getRuntimeProviderConfiguration(signal);
				patch((c) => ({ ...c, runtimeProviderConfiguration: payload.providers }));
				return;
			}
			case 'runtimeProviders': {
				const payload = await getRuntimeProviders(signal);
				setRuntimeRef.current(payload);
				return;
			}
			case 'providers': {
				const payload = await getModelGatewayProviders(signal);
				patch((c) => ({ ...c, providers: payload.providers }));
				return;
			}
			case 'models': {
				const payload = await getModelGatewayModels(signal);
				patch((c) => ({ ...c, models: payload.models }));
				return;
			}
			case 'routingProfiles': {
				const payload = await getModelGatewayRoutingProfiles(signal);
				patch((c) => ({ ...c, routingProfiles: payload.routingProfiles }));
				return;
			}
			case 'rolePolicies': {
				const payload = await getModelGatewayRolePolicies(signal);
				patch((c) => ({ ...c, rolePolicies: payload.rolePolicies }));
				return;
			}
			case 'budgetRules': {
				const payload = await getModelGatewayBudgetRules(signal);
				patch((c) => ({ ...c, budgetRules: payload.budgetRules }));
				return;
			}
			case 'providerLimits': {
				const payload = await getModelGatewayProviderLimits(signal);
				patch((c) => ({ ...c, providerLimits: payload.providerLimits }));
				return;
			}
			case 'usageLedger': {
				const payload = await getModelGatewayUsageLedger(signal);
				patch((c) => ({ ...c, usageLedger: payload.usageLedger }));
				return;
			}
			case 'usageSummary': {
				const payload = await getModelGatewayUsageSummary(signal);
				patch((c) => ({ ...c, usageSummary: payload.summary }));
				return;
			}
			case 'benchmarks': {
				const payload = await getModelGatewayBenchmarks(signal);
				patch((c) => ({ ...c, benchmarks: payload.benchmarks }));
				return;
			}
			case 'benchmarkOutcomes': {
				const payload = await getModelGatewayBenchmarkOutcomes(signal);
				patch((c) => ({ ...c, benchmarkOutcomes: payload.outcomes }));
				return;
			}
			case 'routingDecisions': {
				const payload = await getModelGatewayRoutingDecisions(signal);
				patch((c) => ({ ...c, routingDecisions: payload.routingDecisions }));
				return;
			}
			case 'cliRuntimes': {
				const payload = await getModelGatewayCliRuntimes(signal);
				patch((c) => ({ ...c, cliRuntimes: payload.cliRuntimes }));
				return;
			}
			case 'cliSessions': {
				const payload = await getModelGatewayCliSessions(signal);
				patch((c) => ({ ...c, cliSessions: payload.cliSessions }));
				return;
			}
		}
	}, []);

	const load = useCallback(
		async (group: string, resources: ResourceKey[], force: boolean) => {
			const current = slicesRef.current[group];
			const activeController = controllersRef.current.get(group);
			if (!force && current?.status === 'ready') return;
			if (!force && current?.status === 'loading' && activeController?.signal.aborted !== true)
				return;
			const needed = resources.filter((key) => force || !loadedRef.current.has(key));
			if (!needed.length) {
				setSlice(group, { status: 'ready', error: '' });
				return;
			}
			controllersRef.current.get(group)?.abort();
			const controller = new AbortController();
			controllersRef.current.set(group, controller);
			setSlice(group, { status: 'loading', error: '' });
			try {
				await Promise.all(needed.map((key) => fetchResource(key, controller.signal)));
				if (controller.signal.aborted) return;
				for (const key of needed) loadedRef.current.add(key);
				setSlice(group, { status: 'ready', error: '' });
			} catch (error) {
				if (controller.signal.aborted || (error as { name?: string })?.name === 'AbortError')
					return;
				setSlice(group, {
					status: 'error',
					error:
						error instanceof Error
							? error.message
							: tRef.current(
									'app.modelGateway.error.stateLoadFailed',
									'Model Gateway state failed to load.',
								),
				});
			}
		},
		[fetchResource, setSlice],
	);

	const resourcesFor = useCallback(
		(group: ModelGatewayTab | 'overview') =>
			group === 'overview' ? OVERVIEW_RESOURCES : TAB_RESOURCES[group],
		[],
	);

	const ensure = useCallback(
		(group: ModelGatewayTab | 'overview') => {
			void load(group, resourcesFor(group), false);
		},
		[load, resourcesFor],
	);

	const refresh = useCallback(
		(group: ModelGatewayTab | 'overview') => {
			for (const key of resourcesFor(group)) loadedRef.current.delete(key);
			void load(group, resourcesFor(group), true);
		},
		[load, resourcesFor],
	);

	// biome-ignore lint/correctness/useExhaustiveDependencies: ensure is stable; run once on mount.
	useEffect(() => {
		ensure('overview');
	}, []);

	useEffect(() => {
		ensure(activeTab);
		return () => {
			controllersRef.current.get(activeTab)?.abort();
		};
	}, [activeTab, ensure]);

	const slice = useCallback(
		(group: ModelGatewayTab | 'overview'): TabSlice =>
			slices[group] ?? { status: 'idle', error: '' },
		[slices],
	);

	return { slice, ensure, refresh };
}
