/**
 * Botón + diálogo que muestra el spec ejecutable de una historia: responsabilidades por rol
 * (goal, reviewer, dependencias) y el texto de prompt exacto que recibirá el agente que
 * implementa. Obtiene el spec bajo demanda desde el endpoint read-only al abrir el diálogo;
 * el estado vive local al componente para no inflar el estado agregado del product loop.
 * @author Rodrigo Mason
 */

import { FileText } from 'lucide-react';
import { useState } from 'react';

import { getStorySpec, type StorySpecResponse } from '../../../api/client';
import { Button, Dialog, ErrorState, Skeleton } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';

type StorySpecDialogProps = {
	projectId: string;
	storyId: string;
	storyTitle: string;
};

export function StorySpecDialog({ projectId, storyId, storyTitle }: StorySpecDialogProps) {
	const { t } = useI18n();
	const [open, setOpen] = useState(false);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');
	const [spec, setSpec] = useState<StorySpecResponse | null>(null);

	const openDialog = () => {
		setOpen(true);
		if (spec || loading) {
			return;
		}
		setLoading(true);
		setError('');
		getStorySpec(projectId, storyId)
			.then((payload) => setSpec(payload))
			.catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
			.finally(() => setLoading(false));
	};

	return (
		<>
			<Button icon={<FileText size={16} aria-hidden="true" />} onClick={openDialog}>
				{t('app.workbench.loop.storySpec.action', 'Agent spec')}
			</Button>
			<Dialog
				label={`${t('app.workbench.loop.storySpec.title', 'Story spec')}: ${storyTitle}`}
				onClose={() => setOpen(false)}
				open={open}
			>
				<div className="stack">
					{loading ? <Skeleton /> : null}
					{error ? (
						<ErrorState
							body={error}
							title={t('app.workbench.loop.storySpec.error', 'The story spec could not be loaded')}
						/>
					) : null}
					{spec ? (
						<>
							<span className="field-help">
								{t('app.workbench.loop.storySpec.rolesLabel', 'Role responsibilities')}
							</span>
							<div className="stack compact">
								{spec.roleResponsibilities.map((item) => (
									<div className="inline" key={item.taskId}>
										<span className="mono">{item.role}</span>
										<span>{item.goal}</span>
										{item.reviewerRole ? (
											<span className="muted">
												{t('app.workbench.loop.storySpec.reviewer', 'Reviewer')}:{' '}
												{item.reviewerRole}
											</span>
										) : null}
										{item.dependsOn.length ? (
											<span className="muted">
												{t('app.workbench.loop.storySpec.dependsOn', 'depends on')}{' '}
												{item.dependsOn.map((dependency) => dependency.role).join(', ')}
											</span>
										) : null}
									</div>
								))}
							</div>
							<span className="field-help">
								{t(
									'app.workbench.loop.storySpec.promptLabel',
									'Prompt the implementing agent receives',
								)}
							</span>
							<pre className="artifact-preview">{spec.promptText}</pre>
						</>
					) : null}
				</div>
			</Dialog>
		</>
	);
}
