/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useEffect, useMemo } from 'react';
import { ClipboardCheck, Code2, FileCheck2, ListChecks, ShieldCheck, TerminalSquare, Workflow } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { IssueToPatchResponse } from '../../api/client';
import type { Overview, Project, RuntimeProviders } from '../../api/types';
import { useI18n } from '../../i18n/I18nProvider';
import { evidenceDiffChangedFiles } from '../../lib/diff';
import { shortId } from '../../lib/format';
import { buildWorkflowTimeline } from './timelineModel';
import {
	activeProjects,
	deriveBlockers,
	objectRecord,
	runtimeIsExecutableIssueRuntime,
	runtimeSupportsIssueToPatch,
	sortByTimeDesc,
} from './workbenchSelectors';

type JsonRecord = Record<string, unknown>;

/** Sentinel session id meaning "compose a brand-new work session". Shared with the page. */
export const NEW_SESSION_ID = '__new_work_session__';

export function formatTime(value: string | undefined, notRecordedLabel = 'not recorded') {
	if (!value) return notRecordedLabel;
	const parsed = Date.parse(value);
	return Number.isNaN(parsed) ? value : new Date(parsed).toLocaleString();
}

function asRecord(value: unknown): JsonRecord {
	return value && typeof value === 'object' && !Array.isArray(value) ? (value as JsonRecord) : {};
}

function textValue(value: unknown, fallback = 'n/a') {
	if (value === null || value === undefined) return fallback;
	const text = String(value).trim();
	return text || fallback;
}

type ExpertBlueprint = {
	role: string;
	labelKey: string;
	label: string;
	laneKey: string;
	lane: string;
	responsibilityKey: string;
	responsibility: string;
	icon: LucideIcon;
};

const expertBlueprints: ExpertBlueprint[] = [
	{ role: 'product_owner', labelKey: 'app.workbench.team.role.jp', label: 'JP / Product Owner', laneKey: 'app.workbench.team.lane.direction', lane: 'Direction', responsibilityKey: 'app.workbench.team.role.jpBody', responsibility: 'Backlog, priority and acceptance criteria for long-running work.', icon: ClipboardCheck },
	{ role: 'technical_lead', labelKey: 'app.workbench.team.role.tl', label: 'Technical Lead', laneKey: 'app.workbench.team.lane.guidance', lane: 'Guidance', responsibilityKey: 'app.workbench.team.role.tlBody', responsibility: 'Technical strategy, handoffs and quality gates across agents.', icon: Workflow },
	{ role: 'architect_agent', labelKey: 'app.workbench.team.role.architect', label: 'Architecture', laneKey: 'app.workbench.team.lane.design', lane: 'Design', responsibilityKey: 'app.workbench.team.role.architectBody', responsibility: 'Architecture decisions, environment assumptions and risk framing.', icon: ListChecks },
	{ role: 'developer', labelKey: 'app.workbench.team.role.developer', label: 'Engineering', laneKey: 'app.workbench.team.lane.build', lane: 'Build', responsibilityKey: 'app.workbench.team.role.developerBody', responsibility: 'Frontend, backend and implementation work inside the selected workspace.', icon: Code2 },
	{ role: 'devops', labelKey: 'app.workbench.team.role.devops', label: 'DevOps', laneKey: 'app.workbench.team.lane.environment', lane: 'Environment', responsibilityKey: 'app.workbench.team.role.devopsBody', responsibility: 'Local runtime, scripts, containers and release readiness.', icon: TerminalSquare },
	{ role: 'qa_reviewer', labelKey: 'app.workbench.team.role.qa', label: 'QA', laneKey: 'app.workbench.team.lane.validation', lane: 'Validation', responsibilityKey: 'app.workbench.team.role.qaBody', responsibility: 'Tests, evidence packages and regression verdicts.', icon: FileCheck2 },
	{ role: 'security_reviewer', labelKey: 'app.workbench.team.role.security', label: 'Security', laneKey: 'app.workbench.team.lane.guardrails', lane: 'Guardrails', responsibilityKey: 'app.workbench.team.role.securityBody', responsibility: 'Policy, permissions, sandbox posture and sensitive-output review.', icon: ShieldCheck },
];

const deliveryStageBlueprints = [
	{ id: 'intake', labelKey: 'app.workbench.delivery.intake', label: 'Intake', owner: 'JP' },
	{ id: 'planning', labelKey: 'app.workbench.delivery.planning', label: 'Planning', owner: 'TL' },
	{ id: 'architecture', labelKey: 'app.workbench.delivery.architecture', label: 'Architecture and environment', owner: 'Architecture + DevOps' },
	{ id: 'implementation', labelKey: 'app.workbench.delivery.implementation', label: 'Implementation', owner: 'Engineering' },
	{ id: 'validation', labelKey: 'app.workbench.delivery.validation', label: 'QA and security', owner: 'QA + Security' },
	{ id: 'delivery', labelKey: 'app.workbench.delivery.delivery', label: 'Deliverables', owner: 'TL + JP' },
];

function branchFromOverview(project: Project | null, overview: Overview) {
	if (!project) return 'not detected';
	for (const workspace of sortByTimeDesc(overview.runtimeWorkspaces.filter((workspace) => workspace.projectId === project.id))) {
		const metadata = asRecord(workspace.metadata);
		const branch = metadata.branchName ?? metadata.branch ?? metadata.baseBranch;
		if (branch) return textValue(branch);
	}
	for (const run of sortByTimeDesc(overview.workflowRuns.filter((run) => run.projectId === project.id))) {
		const metadata = asRecord(run.metadata);
		const diffSummary = asRecord(metadata.diffSummary);
		const branch = metadata.branch ?? metadata.branchName ?? diffSummary.branch ?? diffSummary.baseBranch;
		if (branch) return textValue(branch);
	}
	return 'not detected';
}

function roleMatches(blueprintRole: string, roleValue: string, idOrName = '') {
	const normalizedRole = roleValue.toLowerCase();
	const normalizedName = idOrName.toLowerCase();
	if (blueprintRole === 'architect_agent') return normalizedRole.includes('architect') || normalizedName.includes('architect');
	if (blueprintRole === 'developer') return ['developer', 'backend_engineer', 'frontend_engineer', 'implementer'].includes(normalizedRole);
	if (blueprintRole === 'qa_reviewer') return normalizedRole === 'qa' || normalizedRole === 'qa_reviewer';
	return normalizedRole === blueprintRole;
}

function stageName(stage: JsonRecord) {
	return textValue(stage.name ?? stage.id ?? stage.stage, 'stage');
}

function stageStatus(stage: JsonRecord) {
	return textValue(stage.status, 'pending');
}

type UseWorkbenchDataParams = {
	overview: Overview;
	selectedProject: Project | null;
	runtimeProviders: RuntimeProviders | null;
	selectedSessionId: string;
	issueResult: IssueToPatchResponse | null;
	issueBusy: boolean;
	selectedRunId: string;
	/** Keeps the session selection valid as projects/sessions change. */
	onResetSession: (sessionId: string) => void;
};

/**
 * Derives every project-scoped collection the Workbench renders from the global
 * overview. Extracted from WorkbenchPage so the page stays a thin layout and the
 * (heavy) derivation logic lives in one testable place.
 */
export function useWorkbenchData({
	overview,
	selectedProject,
	runtimeProviders,
	selectedSessionId,
	issueResult,
	issueBusy,
	selectedRunId,
	onResetSession,
}: UseWorkbenchDataParams) {
	const { t } = useI18n();

	const projects = useMemo(() => activeProjects(overview.projects), [overview.projects]);
	const project = selectedProject ?? projects[0] ?? null;

	const projectTeams = useMemo(() => (project ? overview.teams.filter((team) => team.projectId === project.id) : []), [overview.teams, project]);
	const teamIds = useMemo(() => new Set(projectTeams.map((team) => team.id)), [projectTeams]);
	const primaryTeam = projectTeams[0] ?? null;
	const projectAgents = useMemo(() => overview.agents.filter((agent) => teamIds.has(agent.teamId)), [overview.agents, teamIds]);
	const projectSessions = useMemo(() => sortByTimeDesc(project ? overview.sessions.filter((session) => session.projectId === project.id) : []), [overview.sessions, project]);
	const activeSession = selectedSessionId === NEW_SESSION_ID ? null : projectSessions.find((session) => session.id === selectedSessionId) ?? projectSessions[0] ?? null;
	const projectChats = useMemo(() => sortByTimeDesc(project ? overview.chats.filter((chat) => chat.projectId === project.id) : []), [overview.chats, project]);
	const sessionChats = activeSession ? projectChats.filter((chat) => chat.sessionId === activeSession.id) : projectChats;
	const projectPipelines = useMemo(() => sortByTimeDesc(project ? overview.pipelines.filter((pipeline) => pipeline.projectId === project.id) : []), [overview.pipelines, project]);
	const sessionPipelines = activeSession ? projectPipelines.filter((pipeline) => pipeline.sessionId === activeSession.id || !pipeline.sessionId) : projectPipelines;
	const projectWorkflows = useMemo(() => sortByTimeDesc(project ? overview.workflows.filter((workflow) => workflow.projectId === project.id) : []), [overview.workflows, project]);
	const projectWorkflowRuns = useMemo(() => sortByTimeDesc(project ? overview.workflowRuns.filter((run) => run.projectId === project.id) : []), [overview.workflowRuns, project]);
	const projectWorkflowEvents = useMemo(() => sortByTimeDesc(project ? overview.workflowEvents.filter((event) => event.projectId === project.id) : []), [overview.workflowEvents, project]);
	const projectEvents = useMemo(() => sortByTimeDesc(project ? overview.events.filter((event) => event.projectId === project.id) : []), [overview.events, project]);
	const projectWorkspaces = useMemo(() => sortByTimeDesc(project ? overview.runtimeWorkspaces.filter((workspace) => workspace.projectId === project.id) : []), [overview.runtimeWorkspaces, project]);
	const projectEvidence = useMemo(() => sortByTimeDesc(project ? overview.evidencePackages.filter((evidence) => evidence.projectId === project.id) : []), [overview.evidencePackages, project]);
	const projectArtifacts = useMemo(() => sortByTimeDesc(project ? overview.artifacts.filter((artifact) => artifact.projectId === project.id) : []), [overview.artifacts, project]);
	const projectTestResults = useMemo(() => (project ? overview.testResultRecords.filter((result) => result.projectId === project.id) : []), [overview.testResultRecords, project]);
	const projectAgentRuns = useMemo(() => sortByTimeDesc(project ? overview.agentRuns.filter((run) => run.projectId === project.id) : []), [overview.agentRuns, project]);
	const pendingApprovals = useMemo(() => (project ? overview.actionRequests.filter((item) => item.projectId === project.id && item.status === 'pending') : []), [overview.actionRequests, project]);

	const runtimeRows = runtimeProviders?.providers ?? [];
	const executableRuntimes = useMemo(() => runtimeRows.filter(runtimeIsExecutableIssueRuntime), [runtimeRows]);
	const hasExecutableRuntime = executableRuntimes.length > 0;
	const unavailableIssueRuntime = runtimeRows.find((runtime) => runtimeSupportsIssueToPatch(runtime) && runtime.executable !== true);
	const runtimeBlockReason = runtimeProviders
		? unavailableIssueRuntime?.reason ?? t('app.workbench.task.runtimeNoExecutable', 'No executable issue_to_patch/code_edit runtime is configured.')
		: t('app.workbench.task.runtimeDiscovery', 'Runtime provider discovery has not completed.');

	const detectedBranch = branchFromOverview(project, overview);
	const branch = detectedBranch === 'not detected' ? t('app.workbench.workspace.branchNotDetected', 'not detected') : detectedBranch;

	const issueEvidenceId = String(objectRecord(issueResult?.evidencePackage)?.id ?? '');
	const selectedRunEvidence = selectedRunId ? projectEvidence.find((evidence) => evidence.workflowRunId === selectedRunId) : null;
	const resolvedEvidenceId = issueEvidenceId || selectedRunEvidence?.id || projectEvidence[0]?.id || '';
	const activeEvidence = useMemo(() => projectEvidence.find((evidence) => evidence.id === resolvedEvidenceId) ?? null, [projectEvidence, resolvedEvidenceId]);
	const reviewChangedFiles = evidenceDiffChangedFiles(activeEvidence);
	const latestEvidence = projectEvidence[0] ?? null;
	const latestRunStatus = String(issueResult?.status ?? projectWorkflowRuns[0]?.status ?? 'idle');

	const blockers = useMemo(
		() => (project ? deriveBlockers({ projectId: project.id, workflowRuns: overview.workflowRuns, testResults: overview.testResultRecords, risks: overview.riskRegister, hasExecutableRuntime, runtimeBlockReason }) : []),
		[overview.workflowRuns, overview.testResultRecords, overview.riskRegister, project, hasExecutableRuntime, runtimeBlockReason],
	);

	useEffect(() => {
		if (!project) {
			onResetSession('');
			return;
		}
		if (selectedSessionId === NEW_SESSION_ID) return;
		if (selectedSessionId && projectSessions.some((session) => session.id === selectedSessionId)) return;
		onResetSession(projectSessions[0]?.id ?? '');
		// onResetSession is a stable setState; project.id captures project identity.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [project?.id, projectSessions, selectedSessionId]);

	const teamRows = expertBlueprints.map((blueprint) => {
		const matchingProfiles = overview.agentProfiles.filter((profile) => roleMatches(blueprint.role, profile.role, `${profile.id} ${profile.name}`));
		const matchingCatalogAgents = projectAgents.filter((agent) => roleMatches(blueprint.role, agent.role, `${agent.id} ${agent.name}`));
		const profileIds = new Set(matchingProfiles.map((profile) => profile.id));
		const matchingRuns = projectAgentRuns.filter((run) => {
			const metadata = asRecord(run.metadata);
			const input = asRecord(run.input);
			const profileId = textValue(metadata.agentProfileId ?? input.agentProfileId, '');
			const agentId = textValue(metadata.agentId ?? input.agentId, '');
			return profileIds.has(profileId) || roleMatches(blueprint.role, profileId, agentId);
		});
		const latestRun = matchingRuns[0] ?? null;
		const configured = matchingProfiles.length > 0 || matchingCatalogAgents.length > 0 || matchingRuns.length > 0;
		const runtime = matchingProfiles[0]?.runtimeMode ?? matchingCatalogAgents[0]?.providerId ?? t('app.workbench.team.notConnected', 'not connected');
		return {
			...blueprint,
			configured,
			runtime,
			status: latestRun?.status ?? (configured ? 'configured' : t('app.workbench.team.notConnected', 'not connected')),
			activity: latestRun ? `${shortId(latestRun.id)} - ${formatTime(latestRun.updatedAt, t('app.workbenchEvidence.notRecorded', 'not recorded'))}` : '',
		};
	});

	const latestPipeline = sessionPipelines[0] ?? projectPipelines[0] ?? null;
	const deliveryRows = deliveryStageBlueprints.map((stage, index) => {
		const pipelineStage = latestPipeline?.stages.map(asRecord).find((item) => {
			const normalized = stageName(item).toLowerCase();
			return normalized.includes(stage.id) || normalized.includes(stage.label.toLowerCase().split(' ')[0]);
		});
		const status = pipelineStage ? stageStatus(pipelineStage) : index === 0 && latestPipeline ? latestPipeline.status : 'pending';
		return { id: stage.id, label: t(stage.labelKey, stage.label), owner: stage.owner, status };
	});

	const runTimeline = buildWorkflowTimeline(issueResult, issueBusy, hasExecutableRuntime);

	return {
		projects,
		project,
		primaryTeam,
		projectSessions,
		activeSession,
		projectChats,
		sessionChats,
		sessionPipelines,
		projectWorkflows,
		projectWorkflowRuns,
		projectWorkflowEvents,
		projectEvents,
		projectWorkspaces,
		projectEvidence,
		projectArtifacts,
		projectTestResults,
		pendingApprovals,
		hasExecutableRuntime,
		branch,
		resolvedEvidenceId,
		activeEvidence,
		reviewChangedFiles,
		latestEvidence,
		latestRunStatus,
		blockers,
		teamRows,
		deliveryRows,
		runTimeline,
	};
}
