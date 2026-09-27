/**
 * Composer chip that states the thread's AI team ("AI team · 2 of 5") and opens the team drawer.
 * Without a thread team it says the global AI team applies ("AI team · global") and its title
 * summarizes the effective PO and Developer providers.
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
import { useRuntimeTeam } from './useRuntimeTeam';
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
	const { data: globalTeam } = useRuntimeTeam(projectId, !open && selection === null);
	const roleLabel = (role: string) => {
		const item = globalTeam?.roles.find((entry) => entry.role === role);
		if (!item?.assigned) return null;
		return (
			globalTeam?.candidates.find((candidate) => candidate.providerId === item.assigned)?.label ??
			item.assigned
		);
	};
	const productOwner = roleLabel('product_owner');
	const developer = roleLabel('developer');
	const globalSummary = [
		productOwner ? `PO: ${productOwner}` : null,
		developer ? `Dev: ${developer}` : null,
	]
		.filter(Boolean)
		.join(' · ');
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
		: t('app.runtimeTeam.chipGlobal', 'AI team · global');
	return (
		<>
			<button
				type="button"
				className="composer-env composer-runtimes runtime-team-chip"
				data-tone={unvalidated > 0 ? 'warn' : undefined}
				aria-haspopup="dialog"
				title={selection ? undefined : globalSummary || undefined}
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
