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
	[
		'local_server_unreachable',
		{
			key: 'app.runtime.health.reason.local_server_unreachable',
			fallback:
				'The local server is not answering. Start it and probe again; under WSL, check localhost forwarding in .wslconfig and the Windows firewall.',
		},
	],
	[
		'model_loading',
		{
			key: 'app.runtime.health.reason.model_loading',
			fallback: 'The server is still loading the model; try again when it finishes.',
		},
	],
	[
		'local_model_load_failed',
		{
			key: 'app.runtime.health.reason.local_model_load_failed',
			fallback: 'The server could not load the model; check the server log and the model file.',
		},
	],
	[
		'local_auth_required',
		{
			key: 'app.runtime.health.reason.local_auth_required',
			fallback:
				'The server requires a token: add a reference to the key the server was started with.',
		},
	],
	[
		'context_length_exceeded',
		{
			key: 'app.runtime.health.reason.context_length_exceeded',
			fallback:
				"The request exceeded the model's context window; use a model or server setting with a larger context.",
		},
	],
	[
		'insecure_credential_transport',
		{
			key: 'app.runtime.health.reason.insecure_credential_transport',
			fallback:
				'A token is never sent over plain http to a remote host; use https or declare the endpoint as running on this machine.',
		},
	],
	[
		'local_endpoint_busy',
		{
			key: 'app.runtime.health.reason.local_endpoint_busy',
			fallback:
				'The local server is already serving another AIDO call; this call waited and gave up.',
		},
	],
	[
		'insufficient_time_for_model_load',
		{
			key: 'app.runtime.health.reason.insufficient_time_for_model_load',
			fallback: 'Not enough execution time is left to load the model; retry the step.',
		},
	],
	[
		'local_model_not_validated',
		{
			key: 'app.runtime.health.reason.local_model_not_validated',
			fallback: 'No model of this runtime is validated for this role; validate a model first.',
		},
	],
	[
		'local_model_not_selected',
		{
			key: 'app.runtime.health.reason.local_model_not_selected',
			fallback: 'Another model of this runtime was chosen for this role.',
		},
	],
]);

/** Text for one reason code, or the code itself when it has no known copy. */
export function describeReasonCode(code: string, t: Translate): string {
	const copy = REASON_COPY.get(code);
	return copy ? t(copy.key, copy.fallback) : code;
}

/** A reason that leads with a machine code and adds detail, as `LocalRuntimeError` formats it. */
const LEADING_CODE_RE = /^([a-z][a-z0-9_]*)[:\s]\s*([\s\S]+)$/;

/**
 * Renders a reason as text: a comma-joined list of codes becomes their copy; a reason that starts
 * with a known code (`local_server_unreachable: connection refused`) becomes that copy with the
 * detail in parentheses; anything else (unknown codes, prose) stays verbatim.
 */
export function describeReason(reason: string, t: Translate): string {
	const codes = reason.split(', ');
	if (codes.some((code) => REASON_COPY.has(code))) {
		return codes.map((code) => describeReasonCode(code, t)).join(' ');
	}
	const leading = LEADING_CODE_RE.exec(reason.trim());
	if (!leading || !REASON_COPY.has(leading[1])) return reason;
	return `${describeReasonCode(leading[1], t)} (${leading[2].trim()})`;
}
