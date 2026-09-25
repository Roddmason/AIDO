/**
 * "Detect local runtimes" for Providers & CLI: asks the backend to probe 127.0.0.1 on the known
 * llama.cpp, LM Studio, vLLM and compatible ports and lists what answered — server kind, base URL,
 * how many models it lists and whether it is already configured. Nothing is created here: "Set up"
 * opens the local-runtime wizard prefilled with the suggestion, and an unrecognized server needs the
 * operator's explicit confirmation before it can be set up as a generic OpenAI-compatible runtime.
 * @author Rodrigo Mason
 */

import { Radar, Server } from 'lucide-react';
import { useState } from 'react';

import { discoverLocalRuntimes, type LocalRuntimeSuggestion } from '../../api/client';
import { StatusChip as Badge, Button, Checkbox, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import { draftFromSuggestion, errorMessage, type LocalRuntimeDraft } from './localEndpoints';
import { catalogEntry } from './runtimeSetup';

function suggestionKey(suggestion: LocalRuntimeSuggestion): string {
	return `${suggestion.catalogId}:${suggestion.baseUrl}`;
}

export function LocalRuntimeDiscovery({
	token,
	onSetUp,
}: {
	token: string;
	onSetUp: (draft: LocalRuntimeDraft) => void;
}) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [suggestions, setSuggestions] = useState<LocalRuntimeSuggestion[] | null>(null);
	const [confirmed, setConfirmed] = useState<ReadonlySet<string>>(new Set());
	const [busy, setBusy] = useState(false);

	const discover = async () => {
		if (busy) return;
		if (!token) {
			notify({
				title: t(
					'app.localRuntime.discovery.tokenRequired',
					'A local write token is required to detect local runtimes.',
				),
				tone: 'warn',
			});
			return;
		}
		setBusy(true);
		try {
			const payload = await discoverLocalRuntimes(token);
			setSuggestions(payload.suggestions);
			setConfirmed(new Set());
		} catch (error) {
			notify({
				title: t('app.localRuntime.discovery.failed', 'Local runtime detection failed'),
				body: redactVisibleSecret(errorMessage(error), 'local runtime detection failed'),
				tone: 'danger',
			});
		} finally {
			setBusy(false);
		}
	};

	const toggleConfirmed = (key: string, checked: boolean) => {
		setConfirmed((current) => {
			const next = new Set(current);
			if (checked) next.add(key);
			else next.delete(key);
			return next;
		});
	};

	return (
		<section
			className="stack compact"
			aria-label={t('app.localRuntime.discovery.title', 'Local runtimes found')}
		>
			<div className="inline">
				<Button icon={<Radar size={15} />} loading={busy} onClick={() => void discover()}>
					{t('app.localRuntime.discovery.detect', 'Detect local runtimes')}
				</Button>
				<span className="field-help">
					{t(
						'app.localRuntime.discovery.hint',
						'Checks 127.0.0.1 on the usual llama.cpp, LM Studio, vLLM and compatible ports. Nothing is saved until you set one up.',
					)}
				</span>
			</div>
			{suggestions === null ? null : suggestions.length === 0 ? (
				<p className="field-help" role="status">
					{t('app.localRuntime.discovery.none', 'No local runtime answered on this machine.')}
				</p>
			) : (
				<div className="masonry-grid">
					{suggestions.map((suggestion) => {
						const key = suggestionKey(suggestion);
						const unknownServer = suggestion.server === 'unknown_openai_compatible';
						const blocked =
							suggestion.alreadyConfigured ||
							(suggestion.requiresConfirmation && !confirmed.has(key));
						return (
							<article key={key} className="card card--static">
								<div className="card-header">
									<div className="inline">
										<Server aria-hidden="true" size={16} />
										<h4 className="card-title">
											{unknownServer
												? t(
														'app.localRuntime.discovery.unknownServer',
														'Unrecognized OpenAI-compatible server',
													)
												: (catalogEntry(suggestion.catalogId)?.displayName ?? suggestion.catalogId)}
										</h4>
									</div>
									{suggestion.alreadyConfigured ? (
										<Badge tone="ok">
											{t('app.localRuntime.discovery.configured', 'Already configured')}
										</Badge>
									) : null}
								</div>
								<span className="mono">{suggestion.baseUrl}</span>
								<span className="field-help">
									<span className="tnum">{(suggestion.models ?? []).length}</span>{' '}
									{t('app.localRuntime.discovery.models', 'models listed')}
								</span>
								{suggestion.requiresConfirmation ? (
									<Checkbox
										label={t(
											'app.localRuntime.discovery.confirmUnknown',
											'I confirm this is an OpenAI-compatible server I run and trust',
										)}
										checked={confirmed.has(key)}
										onChange={(event) => toggleConfirmed(key, event.target.checked)}
									/>
								) : null}
								<Button
									variant="primary"
									disabled={blocked}
									onClick={() => onSetUp(draftFromSuggestion(suggestion))}
								>
									{t('app.localRuntime.discovery.setUp', 'Set up')}
								</Button>
							</article>
						);
					})}
				</div>
			)}
		</section>
	);
}
