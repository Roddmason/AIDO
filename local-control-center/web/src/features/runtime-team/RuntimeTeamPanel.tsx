/**
 * Drawer body of the thread's AI team: runtimes with their 30-minute validation, the per-role grid
 * preloaded with the backend's automatic split, and the deterministic roles AIDO keeps for itself.
 * Expired runtimes are re-tested when the panel opens; CLI rows warn that a test spends quota. Each
 * row keeps its last test outcome (failed or deferred with the cause, including the resource
 * governor's reason while the queued test waits), and roles the operator edited stay pinned while
 * the automatic ones follow the backend split for the current selection.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { type RuntimeTeamCandidate, validateRuntime } from '../../api/client';
import { Button, Checkbox, SelectField, StatusChip } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import {
	isEligible,
	mergeRoleRuntimes,
	missingRequiredRoles,
	OPTIONAL_TEAM_ROLES,
	REQUIRED_TEAM_ROLES,
	type RoleRuntimes,
	type RuntimeTeamSelection,
	TEAM_ROLES,
	type TeamRole,
	toRoleRuntimes,
	type ValidationView,
	validationTone,
	withRole,
} from './runtimeTeamModel';
import { useRuntimeTeamCandidates } from './useRuntimeTeamCandidates';

type Translate = (key: string, fallback?: string) => string;

function roleLabel(t: Translate, role: TeamRole): string {
	switch (role) {
		case 'product_owner':
			return t('app.runtimeTeam.role.product_owner', 'Product Owner');
		case 'developer':
			return t('app.runtimeTeam.role.developer', 'Developer');
		case 'architect':
			return t('app.runtimeTeam.role.architect', 'Architect');
		default:
			return t('app.runtimeTeam.role.security', 'Security');
	}
}

function validationLabel(t: Translate, status: ValidationView): string {
	switch (status) {
		case 'validated':
			return t('app.runtimeTeam.status.validated', 'Validated');
		case 'stale':
			return t('app.runtimeTeam.status.stale', 'Expired');
		case 'failed':
			return t('app.runtimeTeam.status.failed', 'Failed');
		case 'running':
			return t('app.runtimeTeam.status.running', 'Testing…');
		case 'deferred':
			return t('app.runtimeTeam.status.deferred', 'Deferred');
		case 'policy_denied':
			return t('app.runtimeTeam.status.policy_denied', 'Blocked by project policy');
		default:
			return t('app.runtimeTeam.status.never', 'Not tested');
	}
}

function optionalRoleHelp(t: Translate, role: TeamRole): string | undefined {
	if (role === 'architect') {
		return t(
			'app.runtimeTeam.architectOptional',
			'Optional. Without a runtime, the architecture review does not run for this thread.',
		);
	}
	if (role === 'security') {
		return t(
			'app.runtimeTeam.securityOptional',
			'Optional. Without a runtime, security runs only its deterministic scanners.',
		);
	}
	return undefined;
}

function rowHelp(t: Translate, candidate: RuntimeTeamCandidate): string | undefined {
	if (candidate.eligibleRoles.length === 0) {
		return t('app.runtimeTeam.noRole', 'No team role can use this runtime.');
	}
	if (candidate.kind === 'cli') {
		return t('app.runtimeTeam.cliQuota', 'Testing uses your subscription quota.');
	}
	return undefined;
}

/** Outcome of the last test this panel ran for a row, kept until the next test of that row. */
type ProbeOutcome = { status: ValidationView; reason: string | null; inFlight: boolean };

function latencyText(latencyMs: number | null | undefined): string {
	return latencyMs === null || latencyMs === undefined ? '—' : `${latencyMs} ms`;
}

export type RuntimeTeamPanelProps = {
	projectId: string;
	token: string;
	initial: RuntimeTeamSelection | null;
	onSave: (selection: RuntimeTeamSelection | null) => Promise<void> | void;
	onClose: () => void;
};

export function RuntimeTeamPanel({
	projectId,
	token,
	initial,
	onSave,
	onClose,
}: RuntimeTeamPanelProps) {
	const { t } = useI18n();
	const [allowed, setAllowed] = useState<string[]>(initial?.allowedRuntimes ?? []);
	const [roles, setRoles] = useState<RoleRuntimes>(initial?.roleRuntimes ?? {});
	// A saved team is the operator's choice: its roles start pinned until "Assign automatically".
	const [manualRoles, setManualRoles] = useState<ReadonlySet<TeamRole>>(
		() => new Set(TEAM_ROLES.filter((role) => Boolean(initial?.roleRuntimes[role]))),
	);
	const [probes, setProbes] = useState<Readonly<Record<string, ProbeOutcome>>>({});
	const [saving, setSaving] = useState(false);
	const [saveError, setSaveError] = useState<string | null>(null);
	const { data, failed, reload } = useRuntimeTeamCandidates(projectId, allowed, true);
	const candidates = data?.candidates ?? [];
	const autoProbed = useRef(false);

	const setProbe = useCallback((providerId: string, outcome: ProbeOutcome | null) => {
		setProbes((current) => {
			const next = { ...current };
			if (outcome) next[providerId] = outcome;
			else delete next[providerId];
			return next;
		});
	}, []);

	const probe = useCallback(
		async (providerId: string) => {
			setProbe(providerId, { status: 'running', reason: null, inFlight: true });
			try {
				const { validation } = await validateRuntime(
					token,
					providerId,
					projectId,
					undefined,
					(execution) => {
						if (execution.status === 'resource_wait') {
							setProbe(providerId, {
								status: 'deferred',
								reason: execution.reason || null,
								inFlight: true,
							});
						}
					},
				);
				setProbe(
					providerId,
					validation.status === 'validated'
						? null
						: { status: validation.status, reason: validation.reason ?? null, inFlight: false },
				);
			} catch (error) {
				setProbe(providerId, {
					status: 'failed',
					reason: error instanceof Error ? error.message : String(error),
					inFlight: false,
				});
			} finally {
				reload();
			}
		},
		[token, projectId, reload, setProbe],
	);

	// Operator policy: expired runtimes are re-tested on open; never-tested ones wait for a click.
	useEffect(() => {
		if (!data || autoProbed.current) return;
		autoProbed.current = true;
		for (const candidate of data.candidates) {
			if (candidate.validation.status === 'stale') void probe(candidate.providerId);
		}
	}, [data, probe]);

	useEffect(() => {
		if (!data) return;
		setRoles((current) =>
			mergeRoleRuntimes(
				current,
				toRoleRuntimes(data.suggestedRoleRuntimes),
				data.candidates,
				allowed,
				manualRoles,
			),
		);
	}, [data, allowed, manualRoles]);

	const toggle = (providerId: string, checked: boolean) => {
		setAllowed((current) =>
			checked ? [...current, providerId] : current.filter((id) => id !== providerId),
		);
	};

	const assignAutomatically = () => {
		if (!data) return;
		const none = new Set<TeamRole>();
		setManualRoles(none);
		setRoles(
			mergeRoleRuntimes(
				{},
				toRoleRuntimes(data.suggestedRoleRuntimes),
				data.candidates,
				allowed,
				none,
			),
		);
	};

	const missing = allowed.length > 0 ? missingRequiredRoles(roles, candidates) : [];

	const save = async () => {
		setSaving(true);
		setSaveError(null);
		try {
			await onSave(allowed.length > 0 ? { allowedRuntimes: allowed, roleRuntimes: roles } : null);
			onClose();
		} catch (reason) {
			setSaveError(reason instanceof Error ? reason.message : String(reason));
		} finally {
			setSaving(false);
		}
	};

	const deterministicRoles = [
		t('app.runtimeTeam.role.qa', 'QA'),
		t('app.runtimeTeam.role.devops', 'DevOps'),
		t('app.runtimeTeam.role.techLead', 'Tech Lead'),
	];

	return (
		<div className="drawer-body runtime-team-panel">
			<p className="field-help">
				{t(
					'app.runtimeTeam.intro',
					'Only runtimes that answered a real test in the last 30 minutes can join this thread. Expired ones are re-tested when this panel opens.',
				)}
			</p>
			{failed ? (
				<p className="runtime-team-error" role="alert">
					{t('app.runtimeTeam.loadError', 'Could not load the runtimes. Try again.')}
				</p>
			) : null}
			<ul
				className="runtime-team-list"
				aria-label={t('app.runtimeTeam.runtimes', 'Available runtimes')}
			>
				{candidates.map((candidate) => {
					const outcome = probes[candidate.providerId];
					const running = outcome?.inFlight ?? false;
					const denied = candidate.validation.status === 'policy_denied';
					const status: ValidationView = denied
						? 'policy_denied'
						: outcome
							? outcome.status
							: candidate.validation.status;
					const reason = denied
						? candidate.validation.reason
						: (outcome?.reason ??
							(candidate.validation.status === 'failed' ? candidate.validation.reason : null));
					const checked = allowed.includes(candidate.providerId);
					const selectable =
						candidate.validation.status === 'validated' && candidate.eligibleRoles.length > 0;
					return (
						<li key={candidate.providerId} className="runtime-team-row">
							<Checkbox
								label={candidate.label}
								help={rowHelp(t, candidate)}
								checked={checked}
								disabled={!checked && !selectable}
								onChange={(event) => toggle(candidate.providerId, event.currentTarget.checked)}
							/>
							<StatusChip tone={validationTone(status)}>{validationLabel(t, status)}</StatusChip>
							<span className="runtime-team-latency tnum">
								{latencyText(candidate.validation.latencyMs)}
							</span>
							<Button
								loading={running}
								disabled={denied}
								onClick={() => void probe(candidate.providerId)}
							>
								{t('app.runtimeTeam.test', 'Test')}
							</Button>
							{reason &&
							(status === 'failed' || status === 'deferred' || status === 'policy_denied') ? (
								<span className="field-help runtime-team-reason">{reason}</span>
							) : null}
						</li>
					);
				})}
			</ul>
			<fieldset className="runtime-team-roles" disabled={allowed.length === 0}>
				<legend>{t('app.runtimeTeam.roles', 'Role assignment')}</legend>
				{TEAM_ROLES.map((role) => {
					const options = candidates.filter(
						(candidate) => allowed.includes(candidate.providerId) && isEligible(candidate, role),
					);
					return (
						<SelectField
							key={role}
							label={roleLabel(t, role)}
							value={roles[role] ?? ''}
							onChange={(event) => {
								const value = event.currentTarget.value;
								setManualRoles((current) => new Set(current).add(role));
								setRoles((current) => withRole(current, role, value));
							}}
							help={OPTIONAL_TEAM_ROLES.includes(role) ? optionalRoleHelp(t, role) : undefined}
							error={
								allowed.length > 0 && REQUIRED_TEAM_ROLES.includes(role) && !roles[role]
									? t(
											'app.runtimeTeam.roleRequired',
											'This role needs a runtime before the thread can run.',
										)
									: undefined
							}
						>
							<option value="">{t('app.runtimeTeam.choose', 'Choose a runtime')}</option>
							{options.map((candidate) => (
								<option key={candidate.providerId} value={candidate.providerId}>
									{candidate.label}
								</option>
							))}
						</SelectField>
					);
				})}
				{deterministicRoles.map((label) => (
					<p key={label} className="runtime-team-fixed">
						<span>{label}</span>
						<span>{t('app.runtimeTeam.deterministic', 'AIDO (deterministic)')}</span>
					</p>
				))}
				<Button onClick={assignAutomatically} disabled={!data}>
					{t('app.runtimeTeam.autoAssign', 'Assign automatically')}
				</Button>
			</fieldset>
			{missing.length > 0 ? (
				<p className="field-help" role="status">
					{t(
						'app.runtimeTeam.missingRoles',
						'Product Owner and Developer need a runtime before the thread can run.',
					)}
				</p>
			) : null}
			{saveError ? (
				<p className="runtime-team-error" role="alert">
					{saveError}
				</p>
			) : null}
			<div className="runtime-team-actions">
				<Button
					disabled={allowed.length === 0}
					onClick={() => {
						setAllowed([]);
						setRoles({});
						setManualRoles(new Set());
					}}
				>
					{t('app.runtimeTeam.clear', 'Use automatic routing')}
				</Button>
				<Button
					variant="primary"
					loading={saving}
					disabled={missing.length > 0}
					onClick={() => void save()}
				>
					{t('app.runtimeTeam.save', 'Save team')}
				</Button>
			</div>
		</div>
	);
}
