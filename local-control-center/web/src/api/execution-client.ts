/**
 * Resolves queued domain operations with short, cancellable HTTP reads.
 * Aborting observation never cancels durable server work; cancellation is an explicit control.
 * @author Rodrigo Mason
 */
import {
	type ApiOperationId,
	EXECUTION_OPERATIONS,
	type ExecutionAccepted,
	type ExecutionResponse,
	type GeneratedRequestOptions,
	type OperationRequestBody,
	type OperationResult,
	requestGeneratedOperation,
} from './generated/openapi';

function waitForPoll(signal?: AbortSignal): Promise<void> {
	return new Promise((resolve, reject) => {
		const abort = () => {
			clearTimeout(timer);
			signal?.removeEventListener('abort', abort);
			reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'));
		};
		const timer = setTimeout(() => {
			signal?.removeEventListener('abort', abort);
			resolve();
		}, 1000);
		signal?.addEventListener('abort', abort, { once: true });
		if (signal?.aborted) abort();
	});
}

export class ExecutionObservationError extends Error {
	constructor(
		public readonly executionId: string,
		message: string,
	) {
		super(message);
		this.name = 'ExecutionObservationError';
	}
}

export async function requestCompletedOperation<
	T extends ApiOperationId,
	TResponse = OperationResult<T>,
>(
	operationId: T,
	options: GeneratedRequestOptions<OperationRequestBody<T>> = {},
): Promise<TResponse> {
	const response = await requestGeneratedOperation<T, TResponse | ExecutionAccepted>(
		operationId,
		options,
	);
	if (
		!EXECUTION_OPERATIONS[operationId] ||
		!response ||
		typeof response !== 'object' ||
		!('executionId' in response)
	) {
		return response as TResponse;
	}
	const executionId = String(response.executionId);
	// Bounded observation, not a timeout/cancellation of the server execution.
	const deadline = Date.now() + 30 * 60 * 1000;
	while (Date.now() < deadline) {
		const execution: ExecutionResponse = await requestGeneratedOperation(
			'execution_api_v1_executions__execution_id__get',
			{ pathParams: { execution_id: executionId }, signal: options.signal },
		);
		if (typeof window !== 'undefined') {
			window.dispatchEvent(new CustomEvent('aido:execution', { detail: execution }));
		}
		if (execution.status === 'completed' && (execution.resultStatusCode ?? 500) < 400) {
			return execution.result as TResponse;
		}
		if (['completed', 'failed', 'blocked', 'cancelled', 'interrupted'].includes(execution.status)) {
			const result = execution.result;
			const detail =
				result && typeof result === 'object' && 'detail' in result ? result.detail : null;
			throw new ExecutionObservationError(
				executionId,
				typeof detail === 'string' ? detail : execution.reason || execution.status,
			);
		}
		await waitForPoll(options.signal);
	}
	throw new ExecutionObservationError(
		executionId,
		`Observation timed out. Execution ${executionId} remains available in Operations.`,
	);
}
