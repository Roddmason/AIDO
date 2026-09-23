/**
 * Composer chip that states the thread's AI team ("AI team · 2 of 5") and opens the team drawer.
 * A new thread keeps the selection as a draft that the intake persists before the first message;
 * an existing thread saves it straight away through the run-configuration PATCH.
 * @author Rodrigo Mason
 */
import { Users } from 'lucide-react';
import { useState } from 'react';

import { Drawer } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { RuntimeTeamPanel } from './RuntimeTeamPanel';
import type { RuntimeTeamSelection } from './runtimeTeamModel';
import { useRuntimeTeamCandidates } from './useRuntimeTeamCandidates';

export type RuntimeTeamChipProps = {
	projectId: string;
	token: string;
	selection: RuntimeTeamSelection | null;
	onChange: (selection: RuntimeTeamSelection | null) => Promise<void> | void;
	disabled?: boolean;
};

export function RuntimeTeamChip({
	projectId,
	token,
	selection,
	onChange,
	disabled = false,
}: RuntimeTeamChipProps) {
	const { t } = useI18n();
	const [open, setOpen] = useState(false);
	const { data } = useRuntimeTeamCandidates(projectId, selection?.allowedRuntimes ?? null, !open);
	const total = data?.candidates.length ?? 0;
	const unvalidated =
		data && selection
			? selection.allowedRuntimes.filter(
					(id) =>
						data.candidates.find((candidate) => candidate.providerId === id)?.validation.status !==
						'validated',
				).length
			: 0;
	const label = selection
		? t('app.runtimeTeam.chipCount', 'AI team · {selected} of {total}')
				.replace('{selected}', String(selection.allowedRuntimes.length))
				.replace('{total}', String(total))
		: t('app.runtimeTeam.chipAuto', 'AI team · automatic');
	return (
		<>
			<button
				type="button"
				className="composer-env composer-runtimes runtime-team-chip"
				data-tone={unvalidated > 0 ? 'warn' : undefined}
				aria-haspopup="dialog"
				disabled={disabled}
				onClick={() => setOpen(true)}
			>
				<Users aria-hidden="true" size={13} />
				<span className="composer-runtimes-label">{label}</span>
			</button>
			<Drawer
				open={open}
				onClose={() => setOpen(false)}
				label={t('app.runtimeTeam.title', 'AI team for this thread')}
			>
				<RuntimeTeamPanel
					projectId={projectId}
					token={token}
					initial={selection}
					onSave={onChange}
					onClose={() => setOpen(false)}
				/>
			</Drawer>
		</>
	);
}
