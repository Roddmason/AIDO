/**
 * Shared presentation contracts for the threads shell: the author/tone metadata for every
 * `ThreadMessage` kind plus the defensive JSON-value readers used to humanize event payloads.
 * Kept apart from the components so the transcript (ThreadConversation) and the execution panel
 * (ThreadExecutionPanel) render the same message identity without importing each other.
 * @author Rodrigo Mason
 */
import type { ThreadMessage } from '../../api/types';

export type MessageTone = 'ok' | 'warn' | 'danger' | 'info' | 'pending';
export type MessageMeta = { authorKey: string; tone: MessageTone };

/** Message kinds that belong to the conversation transcript; every other kind is execution telemetry. */
export const TRANSCRIPT_KINDS: ReadonlySet<ThreadMessage['kind']> = new Set([
	'user',
	'aido_lead',
	'decision_request',
	'artifact',
]);

export const MESSAGE_META: Record<ThreadMessage['kind'], MessageMeta> = {
	user: { authorKey: 'app.threads.authorUser', tone: 'info' },
	aido_lead: { authorKey: 'app.threads.authorLead', tone: 'ok' },
	agent_summary: { authorKey: 'app.threads.authorAgent', tone: 'info' },
	decision_request: { authorKey: 'app.threads.decisionTitle', tone: 'warn' },
	artifact: { authorKey: 'app.threads.authorArtifact', tone: 'info' },
	error: { authorKey: 'app.threads.authorError', tone: 'danger' },
	system_event: { authorKey: 'app.threads.authorSystem', tone: 'pending' },
};

export function isRecord(value: unknown): value is Record<string, unknown> {
	return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

export function safeRecord(value: unknown): Record<string, unknown> {
	return isRecord(value) ? value : {};
}

export function arrayValue(value: unknown): unknown[] {
	return Array.isArray(value) ? value : [];
}

export function textValue(value: unknown): string | undefined {
	return typeof value === 'string' && value.trim() ? value : undefined;
}
