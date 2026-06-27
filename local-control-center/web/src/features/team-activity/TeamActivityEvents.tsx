/**
 * Progressive-disclosure footer for a Team Activity card.
 *
 * The headline fields stay always-visible; the noisy detail is folded away. Two collapsed-by-default
 * disclosures keep low-level model/tool events and the raw run payloads out of the way until a
 * developer asks for them. All free text is re-masked through `redactVisibleSecret` before it reaches
 * the DOM — the backend already redacts at write time, so this is a defensive second layer.
 * @author Rodrigo Mason
 */

import type { TeamActivityEntry } from '../../api/types';
import { Disclosure } from '../../components/Disclosure';
import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret, toneForStatus } from '../../lib/format';

/** Pretty-prints an already-redacted structured payload, masking defensively before display. */
function redactedJson(value: unknown): string {
	return redactVisibleSecret(JSON.stringify(value ?? {}, null, 2), '{}');
}

/**
 * A labelled developer payload block: a visible heading followed by the redacted JSON. The block
 * wraps and grows with its content (no fixed-height scroll) so keyboard and screen-reader users can
 * reach every line without a focus trap (WCAG 2.1.1); it stays out of the way behind a collapsed
 * disclosure.
 */
function DevPayload({ label, value }: { label: string; value: unknown }) {
	return (
		<>
			<p className="activity-dev-label">{label}</p>
			<pre className="activity-pre">{redactedJson(value)}</pre>
		</>
	);
}

/** The collapsed low-level events and developer details behind one activity entry. */
export function TeamActivityEvents({ entry }: { entry: TeamActivityEntry }) {
	const { t } = useI18n();
	const modelCalls = entry.lowLevelEvents?.modelCalls ?? [];
	const toolCalls = entry.lowLevelEvents?.toolCalls ?? [];
	const eventSummary = t('app.teamActivity.eventsSummary', '{model} model · {tool} tool')
		.replace('{model}', String(entry.modelCallCount))
		.replace('{tool}', String(entry.toolCallCount));

	return (
		<div className="activity-detail">
			<Disclosure title={t('app.teamActivity.lowLevel', 'Low-level events')} summary={eventSummary}>
				{modelCalls.length === 0 && toolCalls.length === 0 ? (
					<p className="activity-empty">
						{t('app.teamActivity.noEvents', 'No model or tool events recorded yet.')}
					</p>
				) : (
					<ul className="activity-events">
						{modelCalls.map((call) => (
							<li className="activity-event" key={call.id}>
								<Badge tone={toneForStatus(call.status)}>{call.status}</Badge>
								<span className="activity-event-name mono">
									{call.provider} · {call.model}
								</span>
								<span className="activity-event-meta mono">
									{t('app.teamActivity.tokenCount', '{n} tokens').replace(
										'{n}',
										String(call.promptTokens + call.completionTokens),
									)}
								</span>
							</li>
						))}
						{toolCalls.map((call) => (
							<li className="activity-event" key={call.id}>
								<Badge tone={toneForStatus(call.status)}>{call.status}</Badge>
								<span className="activity-event-name mono">{call.toolName}</span>
							</li>
						))}
					</ul>
				)}
			</Disclosure>

			<Disclosure
				title={t('app.teamActivity.developerDetails', 'Developer details')}
				summary={t('app.teamActivity.redactedSummary', 'redacted')}
			>
				<div className="activity-dev">
					<DevPayload
						label={t('app.teamActivity.output', 'Structured output')}
						value={entry.developerDetails?.output}
					/>
					<DevPayload
						label={t('app.teamActivity.input', 'Input')}
						value={entry.developerDetails?.input}
					/>
					<DevPayload
						label={t('app.teamActivity.metadata', 'Metadata')}
						value={entry.developerDetails?.metadata}
					/>
				</div>
			</Disclosure>
		</div>
	);
}
