/**
 * Plain-language text for the machine reason codes that readiness and the host resource governor
 * report (`minimum_free_memory`, `host_cpu_saturated`…). Host-capacity codes say the machine lacks
 * room, so they never read as a configuration problem. Shared by the AI health modal and the thread
 * execution panel so both explain the same code with the same words.
 * @author Rodrigo Mason
 */

type Translate = (key: string, fallback?: string) => string;

export const REASON_COPY = new Map<string, { key: string; fallback: string }>([
	[
		'health_check_required',
		{
			key: 'app.runtime.health.reason.health_check_required',
			fallback: 'The runtime has not passed a recent health check.',
		},
	],
	[
		'minimum_free_memory',
		{
			key: 'app.runtime.health.reason.minimum_free_memory',
			fallback:
				'This machine does not have enough free RAM right now; this is not a configuration problem.',
		},
	],
	[
		'hard_memory_floor',
		{
			key: 'app.runtime.health.reason.hard_memory_floor',
			fallback:
				"This machine's free RAM is below its safety floor; this is not a configuration problem.",
		},
	],
	[
		'aggregate_memory_budget',
		{
			key: 'app.runtime.health.reason.aggregate_memory_budget',
			fallback:
				"The AI work already running uses this machine's whole RAM budget; this is not a configuration problem.",
		},
	],
	[
		'minimum_free_disk',
		{
			key: 'app.runtime.health.reason.minimum_free_disk',
			fallback:
				'This machine does not have enough free disk space; this is not a configuration problem.',
		},
	],
	[
		'host_cpu_saturated',
		{
			key: 'app.runtime.health.reason.host_cpu_saturated',
			fallback:
				"This machine's CPU is busy above the admission limit; this is not a configuration problem.",
		},
	],
	[
		'aggregate_cpu_budget',
		{
			key: 'app.runtime.health.reason.aggregate_cpu_budget',
			fallback:
				"The AI work already running uses this machine's whole CPU budget; this is not a configuration problem.",
		},
	],
	[
		'heavy_workload_capacity',
		{
			key: 'app.runtime.health.reason.heavy_workload_capacity',
			fallback: 'Another heavy job is already using the only heavy-work slot.',
		},
	],
	[
		'light_workload_capacity',
		{
			key: 'app.runtime.health.reason.light_workload_capacity',
			fallback: 'The limit of simultaneous light jobs is already in use.',
		},
	],
]);

/** Text for one reason code, or the code itself when it has no known copy. */
export function describeReasonCode(code: string, t: Translate): string {
	const copy = REASON_COPY.get(code);
	return copy ? t(copy.key, copy.fallback) : code;
}

/** Renders a comma-joined list of reason codes as text, keeping unknown codes (and prose) verbatim. */
export function describeReason(reason: string, t: Translate): string {
	const codes = reason.split(', ');
	if (!codes.some((code) => REASON_COPY.has(code))) return reason;
	return codes.map((code) => describeReasonCode(code, t)).join(' ');
}
