/**
 * "Open artifacts" dialog for one sidebar thread: fetches the real thread detail and lists its
 * attached artifacts (kind, title, when) with honest loading/error/empty states. Read-only — it
 * never fabricates rows; an empty thread says so explicitly.
 * @author Rodrigo Mason
 */
import { useEffect, useState } from 'react';
import { getThread, type ThreadDetailResponse } from '../../api/client';
import type { Thread } from '../../api/types';
import { Button, Dialog, EmptyState, Skeleton, StatusChip } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { formatTime } from '../../lib/format';

type ThreadArtifactsDialogProps = {
	/** Thread whose artifacts are shown; null keeps the dialog closed. */
	thread: Thread | null;
	onClose: () => void;
};

/** Lists the selected thread's real artifacts inside a modal dialog. */
export function ThreadArtifactsDialog({ thread, onClose }: ThreadArtifactsDialogProps) {
	const { t } = useI18n();
	const [detail, setDetail] = useState<ThreadDetailResponse | null>(null);
	const [failed, setFailed] = useState(false);
	const [attempt, setAttempt] = useState(0);
	const threadId = thread?.id ?? null;

	// biome-ignore lint/correctness/useExhaustiveDependencies(attempt): intentional extra dependency — bumping `attempt` is how the Retry button re-runs this fetch.
	useEffect(() => {
		setDetail(null);
		setFailed(false);
		if (!threadId) return undefined;
		const controller = new AbortController();
		getThread(threadId, controller.signal)
			.then((response) => setDetail(response))
			.catch(() => {
				if (!controller.signal.aborted) setFailed(true);
			});
		return () => controller.abort();
	}, [threadId, attempt]);

	return (
		<Dialog
			open={thread != null}
			onClose={onClose}
			label={t('app.shell.threads.artifactsTitle', 'Thread artifacts')}
			className="thread-artifacts-dialog"
		>
			{thread ? (
				<div className="thread-artifacts-body">
					<p className="thread-artifacts-thread">{thread.title}</p>
					{failed ? (
						<EmptyState
							title={t('app.shell.threads.artifactsError', 'Could not load the artifacts')}
							body=""
							action={
								<Button variant="secondary" onClick={() => setAttempt((value) => value + 1)}>
									{t('app.threads.retry', 'Retry')}
								</Button>
							}
						/>
					) : !detail ? (
						<>
							<Skeleton className="thread-skeleton-row" />
							<Skeleton className="thread-skeleton-row" />
						</>
					) : detail.artifacts.length ? (
						<ul className="thread-artifacts-list">
							{detail.artifacts.map((artifact) => (
								<li key={artifact.id} className="thread-artifacts-row">
									<StatusChip>{artifact.kind}</StatusChip>
									<span className="thread-artifacts-name">
										{artifact.title || artifact.artifactId}
									</span>
									<span className="thread-artifacts-time">
										{formatTime(
											artifact.createdAt,
											t('app.workbenchEvidence.notRecorded', 'not recorded'),
										)}
									</span>
								</li>
							))}
						</ul>
					) : (
						<EmptyState
							title={t('app.shell.threads.artifactsEmpty', 'No artifacts yet')}
							body={t(
								'app.shell.threads.artifactsEmptyBody',
								'Artifacts appear here as the loop produces them.',
							)}
						/>
					)}
				</div>
			) : null}
		</Dialog>
	);
}
