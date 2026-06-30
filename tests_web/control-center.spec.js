import { expect, test } from '@playwright/test';

const REAL_QA_HASH = 'a'.repeat(64);

function realQaEvidenceFields() {
	return {
		evidenceSource: 'qa_passed_by_command',
		testResults: [
			{
				command: 'uv run pytest tests_py -q',
				status: 'passed',
				execution: 'restricted_subprocess',
				exitCode: 0,
				returnCode: 0,
				toolCallId: 'agent-tool-call-real-qa-web',
				outputArtifactId: 'artifact-real-qa-output-web',
				artifactHashes: {
					stdoutHash: REAL_QA_HASH,
					stderrHash: REAL_QA_HASH,
					outputArtifactHash: REAL_QA_HASH,
				},
				metadata: { permissionDecisionId: 'permission-decision-real-qa-web' },
			},
		],
		toolCalls: [{ id: 'agent-tool-call-real-qa-web', status: 'completed' }],
		policyDecisions: [{ id: 'permission-decision-real-qa-web', decision: 'allow' }],
		artifacts: [{ id: 'artifact-real-qa-output-web', kind: 'test_report', hash: REAL_QA_HASH }],
		hashes: { 'artifact-real-qa-output-web': REAL_QA_HASH },
	};
}

async function createApprovalJob(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const project = await getActiveProject(page);
	const projectId = project.id;

	await page.request.post('/api/v1/jobs', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			kind: 'pipeline.start',
			payload: {
				command: 'pipeline.start',
				pipelineId: 'pipeline-web-smoke',
				approvalRequired: true,
			},
			idempotencyKey: `web-smoke-${Date.now()}`,
		},
	});
}

async function createWorkflowEvidence(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const project = await getActiveProject(page);
	const projectId = project.id;
	const workflowResponse = await page.request.post('/api/v1/workflows', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			title: `Web workflow ${Date.now()}`,
		},
	});
	const { workflow } = await workflowResponse.json();
	const startedResponse = await page.request.post(`/api/v1/workflows/${workflow.id}/start`, {
		headers: { 'X-Local-Control-Token': token },
		data: { reason: 'web smoke' },
	});
	const started = await startedResponse.json();
	const workspaceStep = started.workflowSteps.find((step) => step.name === 'workspace_create');
	await page.request.post('/api/v1/workspaces', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			taskId: `web-story-${Date.now()}`,
			agentId: 'implementer',
			workflowRunId: started.workflowRun.id,
			workflowStepId: workspaceStep.id,
			isolationType: 'git_worktree',
		},
	});
	const evidenceResponse = await page.request.post('/api/v1/evidence', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			workflowRunId: started.workflowRun.id,
			agentId: 'qa_reviewer',
			taskId: 'web-story-evidence',
			testPlan: 'Run web smoke',
			qaVerdict: 'passed',
			...realQaEvidenceFields(),
		},
	});
	const { evidencePackage } = await evidenceResponse.json();
	const artifactName = `web-qa-report-${Date.now()}.md`;
	await page.request.post(`/api/v1/evidence/${evidencePackage.id}/artifacts`, {
		headers: { 'X-Local-Control-Token': token },
		data: {
			kind: 'qa_report',
			name: artifactName,
			content: '# Web QA report\n\nWorkflow inspector artifact smoke.',
			mimeType: 'text/markdown',
		},
	});
	return { ...workflow, workflowRunId: started.workflowRun.id, evidenceId: evidencePackage.id, artifactName };
}

async function createAuditableEvidence(page, { emptyPatch = false, malformedPatch = false } = {}) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const project = await getActiveProject(page);
	const projectId = project.id;
	const suffix = Date.now();
	const workflowResponse = await page.request.post('/api/v1/workflows', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			title: `Auditable evidence workflow ${suffix}`,
		},
	});
	const { workflow } = await workflowResponse.json();
	const startedResponse = await page.request.post(`/api/v1/workflows/${workflow.id}/start`, {
		headers: { 'X-Local-Control-Token': token },
		data: { reason: 'web evidence audit smoke' },
	});
	const started = await startedResponse.json();
	const workspaceStep = started.workflowSteps.find((step) => step.name === 'workspace_create');
	const workspaceResponse = await page.request.post('/api/v1/workspaces', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			taskId: `web-evidence-audit-${suffix}`,
			agentId: 'implementer',
			workflowRunId: started.workflowRun.id,
			workflowStepId: workspaceStep.id,
			isolationType: 'git_worktree',
		},
	});
	const { workspace } = await workspaceResponse.json();
	const jobResponse = await page.request.post('/api/v1/jobs', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			kind: 'workflow.qa',
			workflowRunId: started.workflowRun.id,
			payload: {
				command: 'uv run pytest tests_web/control-center.spec.js',
				workspaceId: workspace.id,
			},
			idempotencyKey: `web-evidence-audit-${suffix}`,
		},
	});
	const { job } = await jobResponse.json();
	const profileId = `web-evidence-profile-${suffix}`;
	await page.request.post('/api/v1/agent-profiles', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			id: profileId,
			name: 'Web Evidence Auditor',
			role: 'qa',
			runtimeMode: 'cli',
			modelPolicyId: 'implementation_default',
			permissionProfile: 'qa',
			allowedTools: ['shell'],
		},
	});
	const agentRunResponse = await page.request.post('/api/v1/agent-runs', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			agentProfileId: profileId,
			taskId: 'web-evidence-audit',
			workflowRunId: started.workflowRun.id,
			input: {
				jobId: job.id,
				workspaceId: workspace.id,
			},
		},
	});
	const { agentRun } = await agentRunResponse.json();
	const secret = ['sk', 'websecret123456'].join('-');
	const bearer = `Bearer ${'abcdefghijk123456789'}`;
	const passwordSecret = ['hunter', '2'].join('');
	const clientSecret = ['client', 'secret', 'value', suffix].join('-');
	const privateKey = ['private', 'key', 'value', suffix].join('-');
	const envApiKey = ['openai', 'key', suffix].join('-');
	const evidenceResponse = await page.request.post('/api/v1/evidence', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			workflowRunId: started.workflowRun.id,
			agentId: 'qa_reviewer',
			agentRunId: agentRun.id,
			jobId: job.id,
			workspaceId: workspace.id,
			runtimeId: 'cli_codex',
			taskId: 'web-evidence-audit',
			testPlan: 'Run evidence detail viewer tests.',
			qaVerdict: 'failed',
			testResults: [{ command: 'uv run pytest tests_web/control-center.spec.js', status: 'failed', exitCode: 1, summary: 'patch viewer failed before implementation' }],
			diffRefs: [{ kind: 'git_patch', name: 'diff.patch', files: ['local-control-center/web/src/features/pages.tsx'] }],
			diffSummary: { filesChanged: emptyPatch ? 0 : 1, patchArtifact: 'diff.patch' },
			runtimeHealth: { provider: 'cli_codex', available: true, executable: true, version: 'codex-test-runtime' },
			modelCalls: [{
				provider: 'openai',
				model: 'gpt-audit',
				status: 'completed',
				metadata: {
					apiKey: secret,
					authorization: bearer,
					clientSecret,
					privateKey,
					env: `OPENAI_API_KEY=${envApiKey}`,
				},
			}],
			toolCalls: [{ tool: 'shell', status: 'completed', metadata: { command: `git diff -- token=${secret} password=${passwordSecret}`, workspace: workspace.path } }],
			policyDecisions: [{ decision: 'allow', reason: 'QA evidence inspection' }],
			approvals: [{ status: 'not_required', reason: 'viewer smoke' }],
		},
	});
	const { evidencePackage } = await evidenceResponse.json();
	const patchContent = emptyPatch ? '\n' : malformedPatch ? [
		'not a unified diff',
		'+export function EvidencePage({ token }) {',
	].join('\n') : [
		'diff --git a/local-control-center/web/src/features/pages.tsx b/local-control-center/web/src/features/pages.tsx',
		'index 1111111..2222222 100644',
		'--- a/local-control-center/web/src/features/pages.tsx',
		'+++ b/local-control-center/web/src/features/pages.tsx',
		'@@ -1,3 +1,3 @@',
		'-export function EvidencePage() {',
		'+export function EvidencePage({ token }) {',
		' }',
	].join('\n');
	const patchResponse = await page.request.post(`/api/v1/evidence/${evidencePackage.id}/artifacts`, {
		headers: { 'X-Local-Control-Token': token },
		data: {
			kind: 'generic_artifact',
			name: 'diff.patch',
			content: patchContent,
			mimeType: 'text/x-patch',
		},
	});
	const { artifact: patchArtifact } = await patchResponse.json();
	await page.request.post(`/api/v1/evidence/${evidencePackage.id}/artifacts`, {
		headers: { 'X-Local-Control-Token': token },
		data: {
			kind: 'generic_artifact',
			name: 'security-findings.json',
			content: JSON.stringify({ findings: [{ severity: 'high', title: 'hardcoded secret', evidence: secret, password: passwordSecret, client_secret: clientSecret }] }, null, 2),
			mimeType: 'application/json',
		},
	});
	await page.request.post(`/api/v1/evidence/${evidencePackage.id}/artifacts`, {
		headers: { 'X-Local-Control-Token': token },
		data: {
			kind: 'generic_artifact',
			name: 'model-call.json',
			content: JSON.stringify({ model: 'gpt-audit', metadata: { apiKey: secret } }, null, 2),
			mimeType: 'application/json',
		},
	});
	return {
		evidenceId: evidencePackage.id,
		workflowRunId: started.workflowRun.id,
		jobId: job.id,
		agentRunId: agentRun.id,
		workspaceId: workspace.id,
		patchHash: patchArtifact.hash,
		secret,
		bearer,
		passwordSecret,
		clientSecret,
		privateKey,
		envApiKey,
	};
}

async function createGovernanceState(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const project = await getActiveProject(page);
	const projectId = project.id;
	const suffix = Date.now();
	const riskTitle = `Policy bypass risk ${suffix}`;
	const nextStepTitle = `Tighten approval telemetry ${suffix}`;
	const decisionTitle = `Keep policy decisions in SQLite ${suffix}`;

	const riskResponse = await page.request.post('/api/v1/risks', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			title: riskTitle,
			severity: 'high',
			mitigation: 'Require mitigation before high risk can be accepted.',
			owner: 'technical_lead',
			tags: ['policy', 'audit'],
		},
	});
	const { risk } = await riskResponse.json();
	const stepResponse = await page.request.post('/api/v1/next-steps', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			title: nextStepTitle,
			priority: 'high',
			owner: 'technical_lead',
			sourceRiskId: risk.id,
		},
	});
	const { nextStep } = await stepResponse.json();
	await page.request.post('/api/v1/architecture-decisions', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			title: decisionTitle,
			status: 'accepted',
			context: 'Governance must be queryable by the dashboard.',
			decision: 'Persist architecture decisions, risks and next steps in SQLite.',
			consequences: 'The operational UI can expose engineering governance as state.',
			linkedRiskIds: [risk.id],
			nextStepIds: [nextStep.id],
		},
	});
	return { decisionTitle, riskTitle, nextStepTitle };
}

async function createRuntimeTrace(page, workflowRunId) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const project = await getActiveProject(page);
	const profileId = `web-cli-${Date.now()}`;

	await page.request.post('/api/v1/agent-profiles', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			id: profileId,
			name: 'Web CLI Runtime',
			role: 'implementer',
			runtimeMode: 'cli',
			modelPolicyId: 'implementation_default',
			permissionProfile: 'dev_safe',
			allowedTools: ['shell'],
		},
	});
	await page.request.post('/api/v1/agent-runs', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId: project.id,
			agentProfileId: profileId,
			taskId: 'web-policy-trace',
			workflowRunId,
			input: {
				toolCalls: [
					{
						tool: 'shell',
						command: 'python --version',
						path: project.path,
						workspacePath: project.path,
					},
				],
			},
		},
	});
}

function issueToPatchApprovalFixture(projectId, suffix, { completeEvidence = true, runStatus = 'evidence_ready', workflowKind = 'issue_to_patch' } = {}) {
	const now = new Date().toISOString();
	const workflowSlug = workflowKind === 'issue_to_pr' ? 'issue-to-pr' : 'issue-to-patch';
	const workflowActionType = workflowKind === 'issue_to_pr' ? 'workflow.issue_to_pr.approve_issue_to_pr' : 'workflow.issue_to_patch.approve_patch';
	const workflowCommand = workflowKind === 'issue_to_pr' ? 'approve_issue_to_pr' : 'approve_patch';
	const workflowId = `wf-${suffix}`;
	const runId = `run-${suffix}`;
	const qaStepId = `step-qa-${suffix}`;
	const reviewStepId = `step-review-${suffix}`;
	const jobId = `job-${suffix}`;
	const agentRunId = `agent-run-${suffix}`;
	const toolCallId = `tool-call-${suffix}`;
	const permissionDecisionId = `permission-${suffix}`;
	const modelCallId = `model-call-${suffix}`;
	const evidenceId = `evidence-${suffix}`;
	const patchArtifactId = `artifact-patch-${suffix}`;
	const securityArtifactId = `artifact-security-${suffix}`;
	const diffRefs = completeEvidence ? [{ kind: 'git_patch', artifactId: patchArtifactId, evidencePackageId: evidenceId, name: 'diff.patch' }] : [];
	const testResults = completeEvidence ? [{
		command: 'uv run pytest tests_py -q',
		status: 'passed',
		execution: 'restricted_subprocess',
		exitCode: 0,
		returnCode: 0,
		toolCallId,
		outputArtifactId: `artifact-qa-${suffix}`,
		artifactHashes: { stdoutHash: REAL_QA_HASH, stderrHash: REAL_QA_HASH, outputArtifactHash: REAL_QA_HASH },
	}] : [];
	const workflow = { id: workflowId, kind: workflowKind, title: `${workflowSlug} workflow ${suffix}`, status: runStatus, metadata: {}, projectId, createdAt: now, updatedAt: now };
	const workflowRun = {
		id: runId,
		workflowId,
		projectId,
		status: runStatus,
		startedAt: now,
		completedAt: null,
		metadata: {
			evidencePackageId: evidenceId,
			originalEvidencePackageId: evidenceId,
			jobId,
			agentRunId,
			workspaceId: `workspace-${suffix}`,
			diffSummary: { changedFiles: ['src/approval.ts'], patchArtifactId, securityFindingsArtifactId: securityArtifactId },
			pullRequest: runStatus === 'pr_created' ? { htmlUrl: `https://github.test/aido/pulls/${suffix}` } : null,
		},
	};
	const workflowSteps = [
		{ id: qaStepId, workflowId, workflowRunId: runId, projectId, name: 'qa_validation', status: 'completed', input: {}, output: {}, metadata: { order: 3 }, agentProfileId: 'qa_reviewer', taskType: 'qa', createdAt: now, updatedAt: now },
		{ id: reviewStepId, workflowId, workflowRunId: runId, projectId, name: 'technical_review', status: runStatus === 'evidence_ready' ? 'ready' : 'completed', input: {}, output: {}, metadata: { order: 4 }, agentProfileId: 'architect_reviewer', taskType: 'review', createdAt: now, updatedAt: now },
	];
	const hasWorkerLease = ['evidence_ready', 'blocked'].includes(runStatus);
	const job = { id: jobId, projectId, kind: `workflow.${workflowKind}`, status: runStatus === 'evidence_ready' ? 'approval_required' : 'approved', workflowRunId: runId, workflowStepId: qaStepId, payload: { command: workflowKind, workflowRunId: runId, evidencePackageId: evidenceId, runtime: { id: 'codex_cli' } }, idempotencyKey: null, leaseOwner: hasWorkerLease ? `worker-${workflowSlug}` : null, leaseExpiresAt: hasWorkerLease ? now : null, createdAt: now, updatedAt: now };
	const jobRun = { id: `job-run-${suffix}`, jobId, providerId: 'codex_cli', status: 'running', startedAt: now, completedAt: null, summary: `Executing real ${workflowKind} job lease.`, metadata: { workflowRunId: runId } };
	const agentRun = { id: agentRunId, projectId, jobId, workflowRunId: runId, workflowStepId: qaStepId, status: runStatus === 'evidence_ready' ? 'evidence_ready' : runStatus, input: { issueText: `Real ${workflowKind} request` }, output: { reason: runStatus === 'blocked' ? 'QA evidence is missing.' : 'Runtime produced evidence.' }, metadata: { agentProfileId: 'developer_agent', runtimeType: 'codex_cli' }, createdAt: now, updatedAt: now };
	const toolCall = { id: toolCallId, agentRunId, toolName: 'shell', status: 'completed', payload: { command: 'git diff -- src/approval.ts', permissionDecisionId }, createdAt: now, updatedAt: now };
	const modelCall = { id: modelCallId, projectId, agentRunId, modelPolicyId: 'implementation_default', provider: 'openai_compatible', model: 'gpt-audit', status: 'completed', promptTokens: 1200, completionTokens: 220, costUsd: 0.016, metadata: { workflowRunId: runId }, createdAt: now };
	const permissionDecision = { id: permissionDecisionId, projectId, workspaceId: `workspace-${suffix}`, agentId: 'developer_agent', role: 'developer', tool: 'shell', command: 'git diff -- src/approval.ts', path: `/.tmp/workspace-${suffix}`, decision: 'allow', riskLevel: 'medium', reason: 'Read-only diff capture is allowed for evidence.', payload: { workflowRunId: runId, categories: ['git_read'] }, createdAt: now };
	const workflowEvents = runStatus === 'blocked' ? [
		{ id: `workflow-event-blocked-${suffix}`, workflowId, workflowRunId: runId, workflowStepId: reviewStepId, projectId, type: 'workflow.run.blocked', payload: { reason: 'QA evidence is missing.', blockedGate: 'technical_review' }, severity: 'warning', createdAt: now },
	] : [];
	const evidencePackage = {
		id: evidenceId,
		projectId,
		workflowRunId: runId,
		workflowStepId: qaStepId,
		agentId: 'developer_agent',
		agentRunId,
		jobId,
		workspaceId: `workspace-${suffix}`,
		runtimeId: 'codex_cli',
		taskId: `${workflowSlug}-${suffix}`,
		testPlan: 'Run real QA before human approval.',
		qaVerdict: 'needs_human_review',
		evidenceSource: completeEvidence ? 'qa_passed_by_command' : 'evidence_collected',
		acceptanceChecklist: [],
		testResults,
		diffRefs,
		diffSummary: {
			changedFiles: completeEvidence ? ['src/approval.ts'] : [],
			patchArtifactId: completeEvidence ? patchArtifactId : null,
			securityFindingsArtifactId: completeEvidence ? securityArtifactId : null,
		},
		toolCalls: completeEvidence ? [{ id: toolCallId, status: 'completed' }] : [],
		policyDecisions: completeEvidence ? [{ id: permissionDecisionId, decision: 'allow' }] : [],
		artifacts: completeEvidence ? [
			{ id: patchArtifactId, kind: 'git_patch', name: 'diff.patch', hash: REAL_QA_HASH },
			{ id: securityArtifactId, kind: 'security_findings', name: 'security-findings.json', hash: REAL_QA_HASH },
		] : [],
		artifactIds: completeEvidence ? [patchArtifactId, securityArtifactId] : [],
		hashes: completeEvidence ? { [patchArtifactId]: REAL_QA_HASH, [securityArtifactId]: REAL_QA_HASH } : {},
		logs: [],
		modelCalls: [],
		riskNotes: [],
		runtimeHealth: { id: 'codex_cli', available: true, executable: true, status: 'evidence_ready' },
		screenshotRefs: [],
		approvals: [],
		createdAt: now,
	};
	const artifact = completeEvidence ? { id: patchArtifactId, projectId, evidencePackageId: evidenceId, kind: 'git_patch', path: `/.tmp/${patchArtifactId}.patch`, hash: REAL_QA_HASH, metadata: { name: 'diff.patch', mimeType: 'text/x-patch', sizeBytes: 190 }, createdAt: now } : null;
	const actionRequest = runStatus === 'evidence_ready' ? {
		id: `action-${suffix}`,
		jobId,
		projectId,
		actionType: workflowActionType,
		status: 'pending',
		riskLevel: 'medium',
		reason: `Review patch evidence and QA before accepting ${workflowKind} output.`,
		command: completeEvidence ? `${workflowCommand} ${suffix}` : `${workflowCommand} missing-evidence ${suffix}`,
		commandArgv: ['workflow', workflowSlug, 'approve'],
		payload: { workflowRunId: runId, workflowStepId: qaStepId, agentRunId: `agent-run-${suffix}`, workspaceId: `workspace-${suffix}`, runtimeId: 'codex_cli', evidencePackageId: evidenceId, diffSummary: evidencePackage.diffSummary },
		evidenceRefs: [evidenceId],
		diffRefs,
		requestedAt: now,
		expiresAt: null,
		decidedAt: null,
		decidedBy: null,
		runtimeId: 'codex_cli',
		workspaceId: `workspace-${suffix}`,
		workspacePath: `/.tmp/workspace-${suffix}`,
		runtime: { id: 'codex_cli', executable: true },
		workspace: { id: `workspace-${suffix}` },
	} : null;
	const patchText = [
		'diff --git a/src/approval.ts b/src/approval.ts',
		'index 1111111..2222222 100644',
		'--- a/src/approval.ts',
		'+++ b/src/approval.ts',
		'@@ -1,3 +1,3 @@',
		'-export const approvalState = "pending";',
		'+export const approvalState = "approved_for_integration";',
	].join('\n');
	const securityArtifact = completeEvidence ? { id: securityArtifactId, projectId, evidencePackageId: evidenceId, kind: 'security_findings', path: `/.tmp/${securityArtifactId}.json`, hash: REAL_QA_HASH, metadata: { name: 'security-findings.json', mimeType: 'application/json', sizeBytes: 100 }, createdAt: now } : null;
	const securityText = JSON.stringify({ status: 'passed', source: 'policy_decisions', findings: [], policyDecisionIds: [permissionDecisionId] }, null, 2);
	return { workflow, workflowRun, workflowSteps, job, jobRun, agentRun, toolCall, modelCall, permissionDecision, workflowEvents, evidencePackage, artifact, securityArtifact, actionRequest, patchText, securityText };
}

async function routeIssueToPatchApprovalOverview(page, fixtures) {
	const overviewResponse = await page.request.get('/api/v1/overview');
	const overview = await overviewResponse.json();
	await page.route('/api/v1/overview', async (route) => {
		await route.fulfill({
			json: {
				...overview,
				workflows: [...fixtures.map((fixture) => fixture.workflow), ...overview.workflows],
				workflowRuns: [...fixtures.map((fixture) => fixture.workflowRun), ...overview.workflowRuns],
				workflowSteps: [...fixtures.flatMap((fixture) => fixture.workflowSteps), ...overview.workflowSteps],
				workflowEvents: [...fixtures.flatMap((fixture) => fixture.workflowEvents ?? []), ...overview.workflowEvents],
				jobs: [...fixtures.map((fixture) => fixture.job), ...overview.jobs],
				jobRuns: [...fixtures.map((fixture) => fixture.jobRun), ...overview.jobRuns],
				actionRequests: [...fixtures.map((fixture) => fixture.actionRequest).filter(Boolean), ...overview.actionRequests],
				evidencePackages: [...fixtures.map((fixture) => fixture.evidencePackage), ...overview.evidencePackages],
				artifacts: [...fixtures.flatMap((fixture) => [fixture.artifact, fixture.securityArtifact].filter(Boolean)), ...overview.artifacts],
				testResultRecords: [...fixtures.flatMap((fixture) => fixture.evidencePackage.testResults), ...overview.testResultRecords],
				agentRuns: [...fixtures.map((fixture) => fixture.agentRun), ...overview.agentRuns],
				agentToolCalls: [...fixtures.map((fixture) => fixture.toolCall), ...overview.agentToolCalls],
				modelCalls: [...fixtures.map((fixture) => fixture.modelCall), ...overview.modelCalls],
				permissionDecisions: [...fixtures.map((fixture) => fixture.permissionDecision), ...overview.permissionDecisions],
				auditEvents: [...fixtures.flatMap((fixture) => fixture.auditEvents ?? []), ...overview.auditEvents],
			},
		});
	});
	for (const fixture of fixtures) {
		if (fixture.artifact && fixture.patchText) {
			await page.route(`/api/v1/evidence/${fixture.evidencePackage.id}/artifacts/${fixture.artifact.id}`, async (route) => {
				await route.fulfill({
					status: 200,
					contentType: 'text/x-patch',
					headers: {
						'X-AIDO-Artifact-Id': fixture.artifact.id,
						'X-AIDO-Artifact-Hash': fixture.artifact.hash,
						'Content-Disposition': 'attachment; filename="diff.patch"',
					},
					body: fixture.patchText,
				});
			});
		}
		if (!fixture.securityArtifact || !fixture.securityText) continue;
		await page.route(`/api/v1/evidence/${fixture.evidencePackage.id}/artifacts/${fixture.securityArtifact.id}`, async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				headers: {
					'X-AIDO-Artifact-Id': fixture.securityArtifact.id,
					'X-AIDO-Artifact-Hash': fixture.securityArtifact.hash,
					'Content-Disposition': 'attachment; filename="security-findings.json"',
				},
				body: fixture.securityText,
			});
		});
	}
}

async function expectNavigationTargetsReachable(page) {
	// Navigation is now via direct hash — verify each page resolves to a visible heading
	// rather than inspecting the removed .ide-nav .nav-item elements.
	const routes = ['#threads', '#workbench', '#models'];
	for (const hash of routes) {
		await page.goto(`/${hash}`);
		await expectControlPlaneLoaded(page);
		// The shell itself must be present; the shell-sidebar is the persistent nav.
		await expect(page.locator('.shell-sidebar')).toBeVisible();
	}
}

async function getWriteToken(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	return token;
}

async function ensureLocalModelGatewayCatalog(page) {
	const token = await getWriteToken(page);
	const providersResponse = await page.request.get('/api/v1/model-gateway/providers');
	expect(providersResponse.status()).toBe(200);
	const providersPayload = await providersResponse.json();
	expect(providersPayload.providers.some((item) => item.providerId === 'ollama')).toBe(true);

	const modelResponse = await page.request.post('/api/v1/model-gateway/models', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			providerId: 'ollama',
			model: 'local_default',
			displayName: 'Ollama local default',
			modelFamily: 'local',
			contextWindow: 32000,
			maxOutputTokens: 4096,
			supportsJson: true,
			supportsStreaming: true,
			effortLevels: ['low', 'medium'],
			freeTier: true,
			freeTierNotes: 'local runtime cost only',
			enabled: true,
			source: 'web_test_setup',
		},
	});
	expect(modelResponse.status()).toBe(201);
	const { model } = await modelResponse.json();
	expect(model.providerId).toBe('ollama');
	expect(model.model).toBe('local_default');
	expect(model.enabled).toBe(true);
}

async function getActiveProject(page) {
	const projectsResponse = await page.request.get('/api/v1/projects');
	const { projects } = await projectsResponse.json();
	const project = projects.find((item) => item.status === 'active');
	expect(project).toBeTruthy();
	return project;
}

async function createWebProject(page, overrides = {}) {
	const token = await getWriteToken(page);
	const suffix = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
	const body = {
		name: `Web Project ${suffix}`,
		path: `./.tmp/web-project-${suffix}`,
		templateId: 'other',
		createDirectory: true,
		...overrides,
	};
	const response = await page.request.post('/api/v1/projects', {
		headers: { 'X-Local-Control-Token': token },
		data: body,
	});
	expect(response.status()).toBe(201);
	return (await response.json()).project;
}

async function expectControlPlaneLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

test('Control plane shell renders before optional runtime endpoints finish', async ({ page }) => {
	let releaseOptional = () => {};
	const optionalHold = new Promise((resolve) => {
		releaseOptional = resolve;
	});
	await page.route('/api/v1/retrieval/status', async (route) => {
		await optionalHold;
		await route.fulfill({
			json: {
				status: 'configuration_required',
				available: false,
				reason: 'Retrieval is still loading.',
				backend: 'unavailable',
				degraded: false,
				faissAvailable: false,
				indexDir: '',
				indexed: 0,
				dimensions: 0,
			},
		});
	});
	await page.route('/api/v1/runtime/providers', async (route) => {
		await optionalHold;
		await route.fulfill({ json: runtimeProvidersFixture([]) });
	});
	await page.route('/api/v1/runtime/provider-configuration', async (route) => {
		await optionalHold;
		await route.fulfill({ json: runtimeProviderConfigurationFixture([]) });
	});

	try {
		await page.goto('/#home');

		await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 5000 });
		await expect(page.getByRole('heading', { name: 'Open or continue a project' })).toBeVisible();
	} finally {
		releaseOptional();
	}
});

async function createModelGatewayTrace(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const project = await getActiveProject(page);
	const projectId = project.id;
	const profileId = `web-manual-${Date.now()}`;

	await page.request.post('/api/v1/agent-profiles', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			id: profileId,
			name: 'Web Manual Runtime',
			role: 'implementer',
			runtimeType: 'manual',
			modelPolicyId: 'implementation_default',
			allowedTools: ['policy.evaluate'],
			permissionProfile: 'dev_safe',
		},
	});
	await page.request.post('/api/v1/model-gateway/role-policies', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			id: 'implementation_default',
			role: 'implementer',
			routingProfileId: 'balanced_best_value',
			preferred: [{ provider: 'ollama', model: 'local_default' }],
			fallback: [],
			maxCostPerTaskUsd: 1,
			maxTokensPerRun: 4000,
			allowRemote: false,
			allowLocal: true,
			allowCli: false,
			allowApi: true,
		},
	});
	await page.request.post('/api/v1/agent-runs', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			agentProfileId: profileId,
			taskId: 'web-model-trace',
			input: { goal: 'record model trace for UI' },
		},
	});
}

test('Active Projects shows only active projects and ignores stale selected project storage', async ({ page }) => {
	const overviewResponse = await page.request.get('/api/v1/overview');
	const overview = await overviewResponse.json();
	const base = overview.projects[0];
	const inactiveProject = {
		...base,
		id: 'project-web-inactive',
		name: 'Dormant Project',
		path: `${base.path}-inactive`,
		status: 'archived',
	};
	const activeProject = {
		...base,
		id: 'project-web-active',
		name: 'Active Ledger Project',
		path: `${base.path}-active`,
		status: 'active',
	};
	await page.route('/api/v1/overview', async (route) => {
		await route.fulfill({ json: { ...overview, projects: [inactiveProject, activeProject], runtimeWorkspaces: [] } });
	});
	await page.route('/api/v1/events', (route) => route.abort());
	await page.addInitScript(() => window.localStorage.setItem('aido:selectedProjectId', 'project-web-inactive'));

	await page.goto('/#workbench');

	await expect(page.locator('.workbench-layout')).toBeVisible();
	await expect(page.getByText('Active Ledger Project').first()).toBeVisible();
	await expect(page.getByText('Dormant Project')).toBeHidden();
});

test('Settings owns selected project and persists it across reloads', async ({ page }) => {
	const project = await createWebProject(page, { name: `Settings Selected ${Date.now()}` });

	// Open the Settings modal via the sidebar footer button.
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	// Navigate to the Project section.
	await dialog.getByRole('button', { name: 'Project' }).click();
	await page.getByLabel('Operational project').selectOption(project.id);
	// Close and reopen to confirm persistence.
	await dialog.getByRole('button', { name: 'Close Settings' }).click();
	await expect(dialog).toBeHidden();
	await page.reload();
	await expectControlPlaneLoaded(page);
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
	await page.getByRole('dialog', { name: 'Settings' }).getByRole('button', { name: 'Project' }).click();

	await expect(page.getByLabel('Operational project')).toHaveValue(project.id);
	await page.getByRole('dialog', { name: 'Settings' }).getByRole('button', { name: 'Close Settings' }).click();
	await page.goto('/#command');
	await expect(page.getByText(project.name).first()).toBeVisible();
});

test('New Project wizard validates input creates project and selects it', async ({ page }) => {
	const suffix = Date.now();
	const projectName = `Wizard Project ${suffix}`;
	const workspaceBasePath = '.tmp';
	const projectDirectoryName = `wizard-project-${suffix}`;

	// Open Settings modal and navigate to the Project section.
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('button', { name: 'Project' }).click();
	await page.getByRole('button', { name: 'New project' }).click();
	await page.locator('.workspace-mode-card').filter({ hasText: 'Create workspace' }).click();

	await page.getByRole('button', { name: 'Next' }).click();
	await expect(page.getByText('Workspace base path is required.')).toBeVisible();
	await page.getByLabel('Workspace base path').fill(workspaceBasePath);
	await page.getByRole('button', { name: 'Next' }).click();
	await expect(page.getByText('Workspace name is required.')).toBeVisible();
	await page.getByLabel('Workspace name').fill(projectDirectoryName);
	await expect(page.getByText(new RegExp(`Final path:.*${projectDirectoryName}`))).toBeVisible();
	await page.getByRole('button', { name: 'Next' }).click();

	await page.getByLabel('Project name').fill(projectName);
	await page.getByLabel('Project template').selectOption('python-fastapi');
	await page.getByRole('button', { name: 'Next' }).click();
	await page.getByRole('button', { name: 'Open in workbench' }).click();

	await expect(page.getByText(projectName).first()).toBeVisible();
	await expect
		.poll(
			async () => {
				const projectsResponse = await page.request.get('/api/v1/projects');
				const { projects } = await projectsResponse.json();
				const created = projects.find((item) => item.name === projectName);
				if (!created) return null;
				return {
					templateId: created.templateId,
					status: created.status,
					pathEndsWithDirectory: created.path.endsWith(projectDirectoryName),
					workspaceBasePath: created.metadata.workspaceBasePath,
					projectDirectoryName: created.metadata.projectDirectoryName,
					creationMode: created.metadata.creationMode,
					workspaceFlow: created.metadata.workspaceFlow,
				};
			},
			{ timeout: 30_000 },
		)
		.toEqual({
			templateId: 'python-fastapi',
			status: 'active',
			pathEndsWithDirectory: true,
			workspaceBasePath: '.tmp',
			projectDirectoryName,
			creationMode: 'new_under_workspace',
			workspaceFlow: 'create_from_zero',
		});
});

test('New Project wizard exposes attach existing mode and discovery controls', async ({ page }) => {
	// Open Settings modal and navigate to the Project section.
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('button', { name: 'Project' }).click();
	await page.getByRole('button', { name: 'New project' }).click();

	const openCard = page.locator('.workspace-mode-card').filter({ hasText: 'Open folder' });
	const createCard = page.locator('.workspace-mode-card').filter({ hasText: 'Create workspace' });
	await expect(openCard).toHaveAttribute('aria-pressed', 'true');
	await expect(page.getByLabel('Workspace folder')).toBeVisible();
	await createCard.click();
	await expect(createCard).toHaveAttribute('aria-pressed', 'true');
	await expect(page.getByLabel('Workspace base path')).toBeVisible();
	await expect(page.getByLabel('Workspace name')).toBeVisible();
	await openCard.click();

	await expect(page.getByLabel('Workspace folder')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Detect project' })).toBeVisible();
});

test('Workspaces shows allocated workspaces without the project catalog', async ({ page }) => {
	await page.goto('/#workspaces');

	await expect(page.getByRole('heading', { name: 'Workspaces', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Allocated workspaces' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Projects' })).toHaveCount(0);
});

test('Go menu exposes the primary destinations and explorer reflects the active area', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	// The Go menu lists all primary destinations.
	await page.getByRole('menuitem', { name: 'Go' }).click();
	for (const name of ['Home', 'Workbench', 'Runs', 'Review board', 'Settings']) {
		await expect(page.getByRole('menuitem', { name, exact: true })).toBeVisible();
	}
	// Close the menu.
	await page.keyboard.press('Escape');

	// Settings hashes now open the modal instead of a dedicated route.
	// Legacy hashes still resolve: #settings-runtime → modal at Providers & CLI section.
	await page.goto('/#settings-runtime');
	await expectControlPlaneLoaded(page);
	await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
	// The persistent shell-sidebar is always present on every page.
	await expect(page.locator('.shell-sidebar')).toBeVisible();
});

test('Go menu has primary destinations and explorer marks the active route', async ({ page }) => {
	// #settings hash now opens the Settings modal on the home page.
	await page.goto('/#settings');
	await expectControlPlaneLoaded(page);
	await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
	await page.getByRole('dialog', { name: 'Settings' }).getByRole('button', { name: 'Close Settings' }).click();

	// The Go menu exposes at least the core destinations.
	await page.getByRole('menuitem', { name: 'Go' }).click();
	for (const name of ['Home', 'Workbench', 'Runs', 'Review board', 'Settings']) {
		await expect(page.getByRole('menuitem', { name, exact: true })).toBeVisible();
	}
	await page.keyboard.press('Escape');

	// Settings item in Go menu opens the modal.
	await page.getByRole('menuitem', { name: 'Go' }).click();
	await page.getByRole('menuitem', { name: 'Settings', exact: true }).click();
	await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
});

test('IDE navigation keeps touch-safe targets across key destinations', async ({ page }) => {
	for (const hash of ['#threads', '#home', '#workbench', '#workflows', '#review-board']) {
		await page.goto(`/${hash}`);
		await expectControlPlaneLoaded(page);
		await expectNavigationTargetsReachable(page);
	}
});

test('IDE shell uses dark modern surfaces and compact navigation', async ({ page }) => {
	await page.goto('/#workbench');

	// Resolve each surface color to true sRGB pixels via a canvas, so the darkness
	// check is agnostic to how the browser serializes oklch()/color()/rgb(). The
	// '#888888' sentinel makes an unparseable color fail loudly, not pass vacuously.
	const shell = await page.evaluate(() => {
		const luminanceOf = (cssColor) => {
			const canvas = document.createElement('canvas');
			canvas.width = 1;
			canvas.height = 1;
			const ctx = canvas.getContext('2d');
			ctx.fillStyle = '#888888';
			ctx.fillStyle = cssColor;
			ctx.fillRect(0, 0, 1, 1);
			const [red, green, blue] = ctx.getImageData(0, 0, 1, 1).data;
			return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
		};
		return {
			colorScheme: window.getComputedStyle(document.documentElement).colorScheme,
			bodyLuminance: luminanceOf(window.getComputedStyle(document.body).backgroundColor),
			mainLuminance: luminanceOf(window.getComputedStyle(document.querySelector('.main-area')).backgroundColor),
		};
	});
	expect(shell.colorScheme).toContain('dark');
	expect(shell.bodyLuminance).toBeLessThan(70);
	expect(shell.mainLuminance).toBeLessThan(70);

	await expect(page.locator('.shell-sidebar')).toBeVisible();
	await expect(page.locator('.workbench-layout')).toBeVisible();
	await expect(page.locator('.rotor-ring')).toHaveCount(0);
	await expect(page.locator('.console-grid')).toHaveCount(0);
});

test('theme toggle switches to light, applies the light surface and persists across reloads', async ({ page }) => {
	// Read color as the browser serializes it (oklch) — compare equality/scheme, not parsed luminance.
	const surface = () =>
		page.evaluate(() => ({
			bodyBg: window.getComputedStyle(document.body).backgroundColor,
			colorScheme: window.getComputedStyle(document.documentElement).colorScheme,
		}));

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
	const dark = await surface();
	expect(dark.colorScheme).toContain('dark');

	await page.getByRole('button', { name: 'Toggle light and dark theme' }).click();
	await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
	const light = await surface();
	expect(light.colorScheme).toContain('light');
	// The light theme actually re-skins the surface (color-space-agnostic).
	expect(light.bodyBg).not.toBe(dark.bodyBg);

	// The choice persists across a reload (applied before first paint).
	await page.reload();
	await expectControlPlaneLoaded(page);
	await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

	await page.getByRole('button', { name: 'Toggle light and dark theme' }).click();
	await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

test('IDE Explorer exposes audit log and workspace settings from the shell', async ({ page }) => {
	// The per-area ExplorerPanel is gone; navigate directly via hash and assert content.
	await page.goto('/#audit');
	await expectControlPlaneLoaded(page);
	await expect(page.getByRole('heading', { name: 'Audit Log' })).toBeVisible();

	// #settings-workspaces now opens the Settings modal at the Workspaces section.
	await page.goto('/#settings-workspaces');
	await expectControlPlaneLoaded(page);
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	await expect(dialog.getByRole('button', { name: 'Workspaces' })).toBeVisible();
});

test('Workbench chat creates a chat intake and linked pipeline', async ({ page }) => {
	const project = await getActiveProject(page);
	const prompt = `Workbench intake ${Date.now()}`;
	await page.goto('/#workbench');

	await expect(page.locator('.workbench-layout')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Work sessions' })).toBeVisible();
	await expect(page.getByRole('radiogroup', { name: 'Task intake mode' })).toHaveCount(0);
	for (const legacyMode of ['Fix bug', 'Add feature', 'Refactor', 'Write tests']) {
		await expect(page.getByRole('radio', { name: legacyMode })).toHaveCount(0);
	}
	for (const legacyLoopAction of ['AIDO decides', 'Review brief', 'Approve backlog', 'Start iteration']) {
		await expect(page.getByRole('button', { name: legacyLoopAction })).toHaveCount(0);
	}

	// The ten product-loop stages are exposed as accessible tabs.
	for (const tabName of [
		'Conversation',
		'Questions',
		'Product brief',
		'Assumptions',
		'Decisions',
		'Architecture',
		'Backlog',
		'Iteration',
		'Execution',
		'Review',
	]) {
		await expect(page.getByRole('tab', { name: new RegExp(`^${tabName}`) })).toBeVisible();
	}

	await expect(page.getByRole('radiogroup', { name: 'Workbench mode' })).toBeVisible();
	await expect(page.getByRole('radio', { name: 'Consultation' })).toBeVisible();
	await expect(page.getByRole('radio', { name: 'Agents / Loop' })).toBeVisible();

	// The Team activity board is reachable from the composer drawer.
	await page.getByRole('button', { name: 'Open Agents / Loop' }).click();
	const teamDialog = page.getByRole('dialog', { name: 'Team activity' });
	await expect(teamDialog).toBeVisible();
	await teamDialog.getByRole('button', { name: 'Close Team activity' }).click();
	await expect(teamDialog).toBeHidden();

	// The project delivery flow lives in the Iteration section.
	await page.getByRole('tab', { name: /^Iteration/ }).click();
	await expect(page.getByRole('heading', { name: 'Project delivery flow' })).toBeVisible();

	// The single composer is always visible: compose the intake directly, no tab switch needed.
	await page.getByLabel('Workspace folder', { exact: true }).selectOption(project.id);
	await page.getByRole('button', { name: 'New work session' }).click();
	await page.getByLabel('What should AIDO do?').fill(prompt);
	await page.getByRole('button', { name: 'Agents / Loop', exact: true }).click();

	await expect(page.getByText('Chat intake created')).toBeVisible({ timeout: 30_000 });
	// The new chat appears in the Conversation section transcript.
	await page.getByRole('tab', { name: /^Conversation/ }).click();
	await expect(page.getByText(prompt).first()).toBeVisible();
	await expect
		.poll(async () => {
			const overviewResponse = await page.request.get('/api/v1/overview');
			const overview = await overviewResponse.json();
			const chat = overview.chats.find((item) => item.prompt === prompt);
			const pipeline = overview.pipelines.find((item) => item.chatId === chat?.id);
			const session = overview.sessions.find((item) => item.id === chat?.sessionId);
			return Boolean(chat && session && pipeline && pipeline.projectId === project.id && pipeline.sessionId === session.id);
		})
		.toBe(true);
});

test('Workbench shows the Git branch detected by the policy-gated Git status endpoint', async ({ page }) => {
	const project = await getActiveProject(page);
	const gitResponse = await page.request.get(`/api/v1/projects/${project.id}/git/status`);
	const gitStatus = await gitResponse.json();
	test.skip(!gitStatus.currentBranch, 'Git status endpoint did not detect a branch for this workspace fixture.');

	await page.goto('/#workbench');
	const explorer = page.getByRole('complementary', { name: 'Workspace explorer' });
	await expect(explorer).toBeVisible();
	const gitWorkspace = page.getByRole('group', { name: 'Git workspace' });
	await expect(gitWorkspace.getByLabel('Git branch')).toHaveValue(gitStatus.currentBranch);
	await expect(gitWorkspace.getByRole('option', { name: 'not detected' })).toHaveCount(0);
});

test('Go menu localizes primary destinations with the ES EN control', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	// Verify EN destinations in the Go menu.
	await page.getByRole('menuitem', { name: 'Go' }).click();
	for (const name of ['Home', 'Workbench', 'Runs', 'Review board', 'Settings']) {
		await expect(page.getByRole('menuitem', { name, exact: true })).toBeVisible();
	}
	await page.keyboard.press('Escape');

	// Switch to Spanish and verify destination labels are localized.
	await page.getByRole('button', { name: 'ES', exact: true }).click();
	await expect(page.locator('html')).toHaveAttribute('lang', 'es');

	await page.getByRole('menuitem', { name: 'Ir' }).click();
	for (const name of ['Inicio', 'Ejecuciones', 'Tablero de revisión', 'Configuración']) {
		await expect(page.getByRole('menuitem', { name, exact: true })).toBeVisible();
	}
	await page.keyboard.press('Escape');
});

test('language control localizes Settings and New Project wizard chrome', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	// Switch to Spanish BEFORE opening the modal so the scrim does not block the language button.
	await page.getByRole('button', { name: 'ES', exact: true }).click();
	await expect(page.locator('html')).toHaveAttribute('lang', 'es');

	// Open the Settings modal — the sidebar button is labeled "Configuración" in ES,
	// and the resulting dialog title ("aria-label") is "Ajustes" (from app.settings.title).
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Configuración' }).click();
	const dialogEs = page.getByRole('dialog', { name: 'Ajustes' });
	await expect(dialogEs).toBeVisible();

	// The Project section "Nuevo proyecto" button should be localized.
	await dialogEs.getByRole('button', { name: 'Proyecto' }).click();
	await expect(page.getByRole('button', { name: 'Nuevo proyecto' })).toBeVisible();

	await page.getByRole('button', { name: 'Nuevo proyecto' }).click();
	await expect(page.locator('.workspace-mode-card').filter({ hasText: 'Abrir carpeta' })).toBeVisible();
	await expect(page.locator('.workspace-mode-card').filter({ hasText: 'Crear workspace' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Siguiente' })).toBeVisible();
	await page.getByRole('button', { name: 'Cancelar' }).click();

	// Close the modal, switch back to English, and reopen to confirm the title localizes back.
	await dialogEs.getByRole('button', { name: 'Cerrar Ajustes' }).click();
	await expect(dialogEs).toBeHidden();
	await page.getByRole('button', { name: 'EN', exact: true }).click();
	await expect(page.locator('html')).toHaveAttribute('lang', 'en');

	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
});

test('language control localizes catalog-backed operational surfaces', async ({ page }) => {
	await page.goto('/#workbench');
	await expectControlPlaneLoaded(page);
	await expect(page.locator('.workbench-layout')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Work sessions' })).toBeVisible();
	await expect(page.getByText('AI project workbench')).toBeVisible();

	await page.getByRole('button', { name: 'ES', exact: true }).click();

	await expect(page.locator('html')).toHaveAttribute('lang', 'es');
	await expect(page.getByRole('heading', { name: 'Sesiones de trabajo' })).toBeVisible();
	await expect(page.getByText('Workbench de proyecto IA')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Work sessions' })).toHaveCount(0);

	await page.getByRole('button', { name: 'EN', exact: true }).click();
	await expect(page.locator('html')).toHaveAttribute('lang', 'en');
	await expect(page.getByRole('heading', { name: 'Work sessions' })).toBeVisible();
});

test('New Project wizard uses IDE workspace import and blocks duplicate workspace names', async ({ page }) => {
	const existing = await getActiveProject(page);

	// Open Settings modal and navigate to the Project section.
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('button', { name: 'Project' }).click();
	await page.getByRole('button', { name: 'New project' }).click();

	await expect(page.locator('.workspace-mode-card').filter({ hasText: 'Open folder' })).toHaveAttribute('aria-pressed', 'true');
	await expect(page.locator('.workspace-mode-card').filter({ hasText: 'Create workspace' })).toBeVisible();
	await expect(page.getByLabel('Workspace folder')).toBeVisible();

	await page.locator('.workspace-mode-card').filter({ hasText: 'Create workspace' }).click();
	await page.getByLabel('Workspace name').fill(existing.name);

	await expect(page.getByText('Workspace name already exists.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();
});

test('project status navigation filters finished error and cancelled projects', async ({ page }) => {
	const overviewResponse = await page.request.get('/api/v1/overview');
	const overview = await overviewResponse.json();
	const base = overview.projects[0];
	const projects = [
		{ ...base, id: 'project-status-active', name: 'Status Active Project', path: `${base.path}-active`, status: 'active' },
		{ ...base, id: 'project-status-finished', name: 'Status Finished Project', path: `${base.path}-finished`, status: 'completed' },
		{ ...base, id: 'project-status-error', name: 'Status Error Project', path: `${base.path}-error`, status: 'failed' },
		{ ...base, id: 'project-status-cancelled', name: 'Status Cancelled Project', path: `${base.path}-cancelled`, status: 'cancelled' },
	];
	await page.route('/api/v1/overview', async (route) => {
		await route.fulfill({ json: { ...overview, projects } });
	});
	await page.route('/api/v1/events', (route) => route.abort());

	// Old per-lane deep link still resolves and opens the matching lane (now via alias).
	await page.goto('/#projects-finished');
	await expect(page.getByRole('heading', { name: 'Finished Projects' })).toBeVisible();
	await expect(page.locator('.masonry-grid').getByText('Status Finished Project')).toBeVisible();
	await expect(page.locator('.masonry-grid').getByText('Status Active Project')).toBeHidden();

	// Lanes are now a SegmentedControl (radiogroup) on a single Projects surface.
	await page.getByRole('radio', { name: 'With error' }).click();
	await expect(page.getByRole('heading', { name: 'Projects With Error' })).toBeVisible();
	await expect(page.locator('.masonry-grid').getByText('Status Error Project')).toBeVisible();
	await expect(page.locator('.masonry-grid').getByText('Status Finished Project')).toBeHidden();

	await page.getByRole('radio', { name: 'Cancelled' }).click();
	await expect(page.getByRole('heading', { name: 'Cancelled Projects' })).toBeVisible();
	await expect(page.locator('.masonry-grid').getByText('Status Cancelled Project')).toBeVisible();
	await expect(page.locator('.masonry-grid').getByText('Status Error Project')).toBeHidden();
});

test('settings separates configuration types and keeps defaults collapsed', async ({ page }) => {
	// #settings-cli now opens the Settings modal at the Providers & CLI section.
	await page.goto('/#settings-cli');
	await expectControlPlaneLoaded(page);

	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();

	// The section nav groups General and Project sections. At least one button for each
	// key configuration category must be present (some names appear in both groups).
	const nav = dialog.locator('nav[aria-label="Settings sections"]');
	await expect(nav.getByRole('button', { name: 'Project', exact: true })).toBeVisible();
	await expect(nav.getByRole('button', { name: 'Providers & CLI', exact: true })).toBeVisible();
	await expect(nav.getByRole('button', { name: 'Autonomy', exact: true })).toBeVisible();
	// Security appears in both General and Project groups; assert at least one is present.
	await expect(nav.getByRole('button', { name: 'Security', exact: true }).first()).toBeVisible();
	await expect(nav.getByRole('button', { name: 'Workspaces', exact: true })).toBeVisible();

	// The Advanced section in General keeps defaults collapsed by default.
	// Use first() since "Advanced" appears in both General and Project nav groups.
	await nav.getByRole('button', { name: 'Advanced', exact: true }).first().click();
	await expect(page.getByText('Backend: FastAPI v1')).toBeHidden();

	await page.getByRole('button', { name: 'Default configurations' }).click();
	await expect(page.getByText('Backend: FastAPI v1')).toBeVisible();
});

test('Settings modal opens from sidebar and closes cleanly', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	// Open via sidebar footer Settings button.
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();

	// Close via the X button.
	await dialog.getByRole('button', { name: 'Close Settings' }).click();
	await expect(dialog).toBeHidden();

	// Re-open via Escape should not open (modal stays closed after Escape is pressed to close).
	// Re-open via sidebar — confirm it opens again cleanly.
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	await expect(dialog).toBeVisible();
	// Escape key also closes the modal.
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
});

test('Settings modal Autonomy section shows level control and inheritance chip', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();

	// Navigate to the Autonomy section.
	await dialog.getByRole('button', { name: 'Autonomy', exact: true }).click();

	// The autonomy.level row renders a "Autonomy level" label and a chip.
	const autonomyRow = page.locator('.setting-row').filter({ hasText: 'Autonomy level' });
	await expect(autonomyRow).toBeVisible();
	await expect(autonomyRow.locator('.setting-chip')).toBeVisible();
});

test('Settings modal Autonomy override and revert flow updates chip text', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('button', { name: 'Autonomy', exact: true }).click();

	const autonomyRow = page.locator('.setting-row').filter({ hasText: 'Autonomy level' });
	await expect(autonomyRow).toBeVisible();

	// In project scope, "Set for this project" reveals the control and marks the value as overridden.
	const setButton = autonomyRow.getByRole('button', { name: 'Set for this project' });
	if (await setButton.isVisible()) {
		await setButton.click();
		// Save the current value as-is to create the project-level override.
		await autonomyRow.getByRole('button', { name: 'Save' }).click();
		// Chip should now show "Overridden · Project".
		await expect(autonomyRow.locator('.setting-chip')).toContainText('Overridden');
		await expect(autonomyRow.locator('.setting-chip-source')).toContainText('Project');

		// Revert clears the override; chip returns to "Inherited · General".
		await autonomyRow.getByRole('button', { name: 'Revert to General' }).click();
		await expect(autonomyRow.locator('.setting-chip')).toContainText('Inherited');
		await expect(autonomyRow.locator('.setting-chip-source')).toContainText('General');
	} else {
		// General scope or already overridden — chip is always present.
		await expect(autonomyRow.locator('.setting-chip')).toBeVisible();
	}
});

test('legacy hash settings-security opens Settings modal at Security section', async ({ page }) => {
	await page.goto('/#settings-security');
	await expectControlPlaneLoaded(page);

	// Modal must open automatically (intercept in hashchange + mount effect).
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	// The Security nav button must be visible in the section nav.
	// Security appears in both General and Project nav groups; use first().
	const nav = dialog.locator('nav[aria-label="Settings sections"]');
	await expect(nav.getByRole('button', { name: 'Security', exact: true }).first()).toBeVisible();
});

test('Settings modal re-seeds the active section on each open', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	const sidebarSettings = page
		.locator('.shell-sidebar-footer')
		.getByRole('button', { name: 'Settings' });
	await sidebarSettings.click();
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	const nav = dialog.locator('nav[aria-label="Settings sections"]');

	// Navigate to a non-default section.
	await dialog.getByRole('button', { name: 'Autonomy', exact: true }).click();
	await expect(nav.getByRole('button', { name: 'Autonomy', exact: true })).toHaveAttribute(
		'aria-current',
		'page',
	);

	// Close, then reopen via the sidebar (which opens at the default section).
	await dialog.getByRole('button', { name: 'Close Settings' }).click();
	await expect(dialog).toBeHidden();
	await sidebarSettings.click();
	await expect(dialog).toBeVisible();

	// The reopened modal must re-seed to the default section, not stay on the last-viewed Autonomy.
	await expect(nav.getByRole('button', { name: 'Autonomy', exact: true })).not.toHaveAttribute(
		'aria-current',
		'page',
	);
});

test('shell renders the editorial control plane', async ({ page }) => {
	await page.goto('/#workbench');
	await expect(page.locator('.workbench-layout')).toBeVisible();
	await expect(page.getByRole('menuitem', { name: 'Go' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible();
});

test('Home gallery leads with the open-folder action', async ({ page }) => {
	await page.goto('/#home');

	await expect(page.getByRole('heading', { name: 'Open or continue a project' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Open folder' }).first()).toBeVisible();
	await expect(page.getByRole('button', { name: 'Create workspace' })).toBeVisible();
	// Home is a card gallery, not a table-driven dashboard.
	await expect(page.locator('.content-frame table')).toHaveCount(0);
});

test('Home gallery is a single masonry wall of cards that opens the workbench', async ({ page }) => {
	const overviewResponse = await page.request.get('/api/v1/overview');
	const overview = await overviewResponse.json();
	const base = overview.projects[0];
	const projects = [
		{ ...base, id: 'home-gallery-active', name: 'Gallery Active Project', path: `${base.path}-gallery`, status: 'active' },
	];
	await page.route('/api/v1/overview', async (route) => {
		await route.fulfill({ json: { ...overview, projects } });
	});
	await page.route('/api/v1/events', (route) => route.abort());

	await page.goto('/#home');
	await expectControlPlaneLoaded(page);

	// Single masonry wall, card-first, no tables and no retired "Recent evidence" jargon band.
	await expect(page.getByRole('heading', { name: 'Pick up where you left off' })).toBeVisible();
	await expect(page.locator('.content-frame table')).toHaveCount(0);
	await expect(page.getByText('Recent evidence')).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'All projects' })).toBeVisible();

	// The active project renders as a workspace card with a de-jargoned "in progress" chip.
	const wall = page.locator('.masonry-grid');
	await expect(wall.getByText('Gallery Active Project')).toBeVisible();
	await expect(wall.getByText('in progress')).toBeVisible();
	await expect(page.getByText('active jobs')).toHaveCount(0);

	// Opening a workspace card takes the user straight to the workbench.
	await page.getByRole('button', { name: 'Open in workbench: Gallery Active Project' }).click();
	await expect(page.locator('.workbench-layout')).toBeVisible();
});

test('Review board requires contextual review before action decision', async ({ page }) => {
	await createApprovalJob(page);
	await page.goto('/#review-board');
	await expect(page.getByRole('region', { name: 'Review board' })).toBeVisible();
	await expect(page.getByText('pipeline.start').first()).toBeVisible();
	await page.getByRole('button', { name: /^Review:/ }).first().click();
	const review = page.getByRole('dialog', { name: 'Action request review' });
	await expect(review).toBeVisible();
	await expect(review.getByRole('table', { name: 'Action request scope' })).toBeVisible();
	await expect(review.getByText('Argv')).toBeVisible();
	await expect(review.getByLabel('Human decision reason')).toBeVisible();
	await expect(review.getByRole('button', { name: 'Approve', exact: true })).toBeDisabled();
	await expect(review.getByRole('button', { name: 'Reject', exact: true })).toBeDisabled();
	await review.getByLabel('Human decision reason').fill('Reviewed command scope and recorded evidence context.');
	await expect(review.getByRole('button', { name: 'Approve', exact: true })).toBeEnabled();
	await expect(review.getByRole('button', { name: 'Reject', exact: true })).toBeEnabled();
	await page.keyboard.press('Escape');
	await page.keyboard.press('Control+Alt+a');
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeVisible();
	await expect(page.getByText('pipeline.start').first()).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeHidden();
});

test('Review board presents four decision columns instead of a technical table', async ({ page }) => {
	await page.goto('/#review-board');
	await expectControlPlaneLoaded(page);
	const board = page.getByRole('region', { name: 'Review board' });
	await expect(board).toBeVisible();
	for (const column of ['Needs review', 'Blocked', 'Ready', 'Done']) {
		await expect(board.getByText(column, { exact: true })).toBeVisible();
	}
	// Card-first board: the approval queue is not a technical table (drawers live outside the region).
	await expect(board.locator('table')).toHaveCount(0);
});

test('Review board approve patch is evidence-first blocked while reject stays open', async ({ page }) => {
	const project = await getActiveProject(page);
	const complete = issueToPatchApprovalFixture(project.id, 'board-complete', { completeEvidence: true });
	const incomplete = issueToPatchApprovalFixture(project.id, 'board-incomplete', { completeEvidence: false });
	let approvePatchCalled = false;
	await routeIssueToPatchApprovalOverview(page, [complete, incomplete]);
	await page.route(`/api/v1/jobs/${complete.job.id}/actions/${complete.actionRequest.id}/approve`, async (route) => {
		await route.fulfill({ status: 202, json: { actionRequest: { ...complete.actionRequest, status: 'approved' }, job: complete.job } });
	});
	await page.route(`/api/v1/workflows/issue-to-patch/${complete.workflowRun.id}/approve`, async (route) => {
		approvePatchCalled = true;
		await route.fulfill({ status: 202, json: { status: 'approved_for_integration', reason: 'approved from UI', workflowRun: { ...complete.workflowRun, status: 'approved_for_integration' } } });
	});

	await page.goto('/#review-board');
	await page.getByRole('button', { name: new RegExp(`Review: ${complete.workflow.title}`) }).click();
	const review = page.getByRole('dialog', { name: 'Action request review' });
	await expect(review.getByRole('heading', { name: 'Full diff before approval' })).toBeVisible();
	await expect(review.getByText('diff --git a/src/approval.ts b/src/approval.ts')).toBeVisible();
	await expect(review.getByRole('heading', { name: 'Security findings before approval' })).toBeVisible();
	await expect(review.getByText('security findings are non-blocking')).toBeVisible();
	await expect(review.getByRole('heading', { name: 'Evidence completeness' })).toBeVisible();
	await expect(review.getByText('evidence_complete')).toBeVisible();
	await expect(review.getByRole('button', { name: 'Approve patch' })).toBeDisabled();
	await expect(review.getByRole('button', { name: 'Reject' })).toBeDisabled();
	await review.getByLabel('Human decision reason').fill('Reviewed full diff and QA evidence.');
	await expect(review.getByRole('button', { name: 'Approve patch' })).toBeEnabled();
	await expect(review.getByRole('button', { name: 'Reject' })).toBeEnabled();
	await review.getByRole('button', { name: 'Approve patch' }).click();
	await expect.poll(() => approvePatchCalled).toBe(true);

	await page.getByRole('button', { name: new RegExp(`Review: ${incomplete.workflow.title}`) }).click();
	const incompleteReview = page.getByRole('dialog', { name: 'Action request review' });
	await incompleteReview.getByLabel('Human decision reason').fill('Evidence is incomplete; reject remains possible.');
	await expect(incompleteReview.getByText('evidence_incomplete')).toBeVisible();
	await expect(incompleteReview.getByRole('button', { name: 'Approve patch' })).toBeDisabled();
	await expect(incompleteReview.getByRole('button', { name: 'Reject' })).toBeEnabled();
});

test('Review board executes issue-to-pr approval, promotion and PR through workflow endpoints', async ({ page }) => {
	const project = await getActiveProject(page);
	const complete = issueToPatchApprovalFixture(project.id, 'board-pr-complete', { completeEvidence: true, workflowKind: 'issue_to_pr' });
	const approved = issueToPatchApprovalFixture(project.id, 'board-pr-approved', { completeEvidence: true, runStatus: 'approved_for_integration', workflowKind: 'issue_to_pr' });
	const promoted = issueToPatchApprovalFixture(project.id, 'board-pr-promoted', { completeEvidence: true, runStatus: 'promoted_to_branch', workflowKind: 'issue_to_pr' });
	let approveCalled = false;
	let promoteCalled = false;
	let prCalled = false;
	await routeIssueToPatchApprovalOverview(page, [complete, approved, promoted]);
	await page.route(`/api/v1/jobs/${complete.job.id}/actions/${complete.actionRequest.id}/approve`, async (route) => {
		await route.fulfill({ status: 202, json: { actionRequest: { ...complete.actionRequest, status: 'approved' }, job: complete.job } });
	});
	await page.route(`/api/v1/workflows/issue-to-pr/${complete.workflowRun.id}/approve`, async (route) => {
		approveCalled = true;
		await route.fulfill({ status: 202, json: { status: 'approved_for_integration', workflowRun: { ...complete.workflowRun, status: 'approved_for_integration' } } });
	});
	await page.route(`/api/v1/workflows/issue-to-pr/${approved.workflowRun.id}/promote`, async (route) => {
		promoteCalled = true;
		const body = route.request().postDataJSON();
		expect(body.reason).toBe('Promote reviewed issue_to_pr from the board.');
		await route.fulfill({ status: 202, json: { status: 'promoted_to_branch', reason: body.reason, workflowRun: { ...approved.workflowRun, status: 'promoted_to_branch' } } });
	});
	await page.route(`/api/v1/workflows/issue-to-pr/${promoted.workflowRun.id}/pull-request`, async (route) => {
		prCalled = true;
		const body = route.request().postDataJSON();
		expect(body.reason).toBe('Create PR from the board.');
		await route.fulfill({ status: 202, json: { status: 'pr_unavailable', reason: 'AIDO_GITHUB_TOKEN is missing.', workflowRun: promoted.workflowRun, pullRequest: null } });
	});

	await page.goto('/#review-board');
	await page.getByRole('button', { name: new RegExp(`Review: ${complete.workflow.title}`) }).click();
	const review = page.getByRole('dialog', { name: 'Action request review' });
	await expect(review.getByRole('button', { name: 'Approve patch' })).toBeDisabled();
	await review.getByLabel('Human decision reason').fill('Reviewed issue_to_pr diff, QA and security evidence.');
	await expect(review.getByRole('button', { name: 'Approve patch' })).toBeEnabled();
	await review.getByRole('button', { name: 'Approve patch' }).click();
	await expect.poll(() => approveCalled).toBe(true);
	await page.keyboard.press('Escape');

	await page.getByRole('button', { name: new RegExp(`Promote branch: ${approved.workflow.title}`) }).click();
	const promoteDrawer = page.getByRole('dialog', { name: 'Run detail' });
	await promoteDrawer.getByLabel('Workflow operation reason').fill('Promote reviewed issue_to_pr from the board.');
	await promoteDrawer.getByRole('button', { name: 'Promote branch', exact: true }).click();
	await expect.poll(() => promoteCalled).toBe(true);
	await page.keyboard.press('Escape');

	await page.getByRole('button', { name: new RegExp(`Create PR: ${promoted.workflow.title}`) }).click();
	const prDrawer = page.getByRole('dialog', { name: 'Run detail' });
	await prDrawer.getByLabel('Workflow operation reason').fill('Create PR from the board.');
	await prDrawer.getByRole('button', { name: 'Create PR', exact: true }).click();
	await expect.poll(() => prCalled).toBe(true);
	await expect(prDrawer.getByText('pr_unavailable').first()).toBeVisible();
	await expect(prDrawer.getByText('AIDO_GITHUB_TOKEN is missing.').first()).toBeVisible();
});

test('Review board previews linked artifacts through the protected endpoint', async ({ page }) => {
	const project = await getActiveProject(page);
	const complete = issueToPatchApprovalFixture(project.id, 'board-artifact', { completeEvidence: true });
	await routeIssueToPatchApprovalOverview(page, [complete]);
	await page.goto('/#review-board');
	await page.getByRole('button', { name: new RegExp(`Review: ${complete.workflow.title}`) }).click();
	const review = page.getByRole('dialog', { name: 'Action request review' });
	await expect(review.getByRole('button', { name: /^Preview artifact/ }).first()).toBeVisible();
	await expect(review.getByRole('button', { name: /^Download artifact/ }).first()).toBeVisible();
	await review.getByRole('button', { name: /^Preview artifact/ }).first().click();
	await expect(review.getByText(/^sha256 /).first()).toBeVisible();
});

test('Review board serializes artifact downloads to a single in-flight request', async ({ page }) => {
	const project = await getActiveProject(page);
	const complete = issueToPatchApprovalFixture(project.id, 'board-dl-serialize', { completeEvidence: true });
	await routeIssueToPatchApprovalOverview(page, [complete]);

	// Hold the patch-artifact response in flight so the download cannot resolve
	// until released, exercising the serialize-while-downloading guard.
	let releaseDownload = () => {};
	const downloadGate = new Promise((resolve) => { releaseDownload = resolve; });
	await page.route(`/api/v1/evidence/${complete.evidencePackage.id}/artifacts/${complete.artifact.id}`, async (route) => {
		await downloadGate;
		await route.fulfill({
			status: 200,
			contentType: 'text/x-patch',
			headers: {
				'X-AIDO-Artifact-Id': complete.artifact.id,
				'X-AIDO-Artifact-Hash': complete.artifact.hash,
				'Content-Disposition': 'attachment; filename="diff.patch"',
			},
			body: complete.patchText,
		});
	});

	await page.goto('/#review-board');
	await page.getByRole('button', { name: new RegExp(`Review: ${complete.workflow.title}`) }).click();
	const review = page.getByRole('dialog', { name: 'Action request review' });
	const patchDownload = review.getByRole('button', { name: 'Download artifact diff.patch' });
	const securityDownload = review.getByRole('button', { name: 'Download artifact security-findings.json' });
	await expect(patchDownload).toBeEnabled();
	await expect(securityDownload).toBeEnabled();

	const downloadPromise = page.waitForEvent('download');
	await patchDownload.click();

	// While the patch download is in flight, BOTH download buttons are disabled
	// so a second concurrent download cannot race the shared downloadingId/error slot.
	await expect(patchDownload).toHaveText('Downloading');
	await expect(patchDownload).toBeDisabled();
	await expect(securityDownload).toBeDisabled();

	releaseDownload();
	await downloadPromise;

	// Once the download resolves, both buttons re-enable.
	await expect(patchDownload).toBeEnabled();
	await expect(securityDownload).toBeEnabled();
});

test('Review board promotes branches and creates PRs for approved runs with reason gating', async ({ page }) => {
	const project = await getActiveProject(page);
	const approved = issueToPatchApprovalFixture(project.id, 'board-approved', { completeEvidence: true, runStatus: 'approved_for_integration' });
	const promoted = issueToPatchApprovalFixture(project.id, 'board-promoted', { completeEvidence: true, runStatus: 'promoted_to_branch' });
	let promoteCalled = false;
	let prCalled = false;
	await routeIssueToPatchApprovalOverview(page, [approved, promoted]);
	await page.route(`/api/v1/workflows/issue-to-patch/${approved.workflowRun.id}/promote`, async (route) => {
		promoteCalled = true;
		const body = route.request().postDataJSON();
		expect(body.reason).toBe('Promote reviewed patch from the board.');
		await route.fulfill({
			status: 202,
			json: { status: 'promoted_to_branch', reason: body.reason, workflowRun: { ...approved.workflowRun, status: 'promoted_to_branch' } },
		});
	});
	await page.route(`/api/v1/workflows/issue-to-patch/${promoted.workflowRun.id}/pull-request`, async (route) => {
		prCalled = true;
		const body = route.request().postDataJSON();
		expect(body.reason).toBe('Create PR from the board.');
		await route.fulfill({
			status: 202,
			json: { status: 'pr_unavailable', reason: 'AIDO_GITHUB_TOKEN is missing.', workflowRun: promoted.workflowRun, pullRequest: null },
		});
	});

	await page.goto('/#review-board');
	await expect(page.getByRole('region', { name: 'Review board' })).toBeVisible();

	// Promote a reviewed/approved run straight from the board — reason-gated.
	await page.getByRole('button', { name: new RegExp(`Promote branch: ${approved.workflow.title}`) }).click();
	const promoteDrawer = page.getByRole('dialog', { name: 'Run detail' });
	await expect(promoteDrawer).toBeVisible();
	await expect(promoteDrawer.getByRole('button', { name: 'Promote branch', exact: true })).toBeDisabled();
	await promoteDrawer.getByLabel('Workflow operation reason').fill('Promote reviewed patch from the board.');
	await expect(promoteDrawer.getByRole('button', { name: 'Promote branch', exact: true })).toBeEnabled();
	await promoteDrawer.getByRole('button', { name: 'Promote branch', exact: true }).click();
	await expect.poll(() => promoteCalled).toBe(true);
	await expect(promoteDrawer.getByText('promoted_to_branch').first()).toBeVisible();
	await page.keyboard.press('Escape');

	// Create a PR from a promoted run — honest pr_unavailable is surfaced on the board.
	await page.getByRole('button', { name: new RegExp(`Create PR: ${promoted.workflow.title}`) }).click();
	const prDrawer = page.getByRole('dialog', { name: 'Run detail' });
	await expect(prDrawer.getByRole('button', { name: 'Create PR', exact: true })).toBeDisabled();
	await prDrawer.getByLabel('Workflow operation reason').fill('Create PR from the board.');
	await prDrawer.getByRole('button', { name: 'Create PR', exact: true }).click();
	await expect.poll(() => prCalled).toBe(true);
	await expect(prDrawer.getByText('pr_unavailable').first()).toBeVisible();
	await expect(prDrawer.getByText('AIDO_GITHUB_TOKEN is missing.').first()).toBeVisible();
});

test('legacy /#jobs route resolves to the Review board surface', async ({ page }) => {
	await page.goto('/#jobs');
	await expectControlPlaneLoaded(page);
	await expect(page.getByRole('region', { name: 'Review board' })).toBeVisible();
	// The Review Board surface is active: verify the Go menu is reachable (presence confirms the menubar rendered).
	await page.getByRole('menuitem', { name: 'Go' }).click();
	await expect(page.getByRole('menuitem', { name: 'Review board', exact: true })).toBeVisible();
	await page.keyboard.press('Escape');
});

test('Event drawer exposes recent operational events', async ({ page }) => {
	await createApprovalJob(page);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.getByRole('menuitem', { name: 'Edit' }).click();
	await page.getByRole('menuitem', { name: 'Events' }).click();
	await expect(page.getByRole('dialog', { name: 'Event drawer' })).toBeVisible();
	await expect(page.getByText('job.created').first()).toBeVisible();
});

test('event drawer filters operational events by query', async ({ page }) => {
	await createApprovalJob(page);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.getByRole('menuitem', { name: 'Edit' }).click();
	await page.getByRole('menuitem', { name: 'Events' }).click();
	await expect(page.getByRole('dialog', { name: 'Event drawer' })).toBeVisible();
	await page.getByLabel('Event filter').fill('job.created');
	await expect(page.getByText('job.created').first()).toBeVisible();
	await page.getByLabel('Event filter').fill('no-such-event-type');
	await expect(page.getByText('No events')).toBeVisible();
});

test('keyboard shortcuts open operational surfaces without mouse navigation', async ({ page }) => {
	await createApprovalJob(page);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.keyboard.press('Control+Alt+A');
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeHidden();

	await page.keyboard.press('Control+Alt+E');
	await expect(page.getByRole('dialog', { name: 'Event drawer' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Event drawer' })).toBeHidden();

	await page.keyboard.press('Control+Alt+W');
	await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
});

test('Memory & Retrieval shows backend status and memory records', async ({ page }) => {
	await page.goto('/#memory');

	await expect(page.getByRole('heading', { name: 'Memory & Retrieval' })).toBeVisible();
	await expect(page.getByText('Retrieval Backend')).toBeVisible();
	await expect(page.getByText('SQLite is canonical')).toBeVisible();
	await expect(page.getByText('Memory items')).toBeVisible();
});

test('mobile layout has no horizontal overflow', async ({ page }) => {
	await page.setViewportSize({ width: 375, height: 812 });
	await page.goto('/#threads');
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
	expect(overflow).toBe(false);
});

test('reduced motion disables non-essential motion', async ({ browser }) => {
	const context = await browser.newContext({ reducedMotion: 'reduce' });
	const page = await context.newPage();
	await page.goto('/#threads');
	// Reduced motion is honored declaratively: useMotionPreference mirrors the OS setting
	// onto <html data-motion> and MotionConfig reducedMotion="user" strips transforms.
	await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced');
	await context.close();
});

test('Workflows shows the catalog and run ledger, and a run opens its steps', async ({ page }) => {
	const workflow = await createWorkflowEvidence(page);
	await page.goto('/#workflows');

	await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
	await expect(page.getByRole('cell', { name: workflow.title, exact: true }).first()).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Workflow runs' })).toBeVisible();

	// The run's steps now live in the shell Inspector (no artificial page-local step graph).
	await page.getByRole('button', { name: `Inspect workflow ${workflow.title}` }).click();
	await expect(page.getByRole('heading', { name: 'Run inspector' })).toBeVisible();
	await page.getByRole('tab', { name: 'Evidence' }).click();
	await expect(page.getByText('workspace_create').first()).toBeVisible();
});

test('a workflow run is deep-linkable into the shell Run inspector', async ({ page }) => {
	const project = await getActiveProject(page);
	const fixture = issueToPatchApprovalFixture(project.id, 'deeplink', {
		completeEvidence: true,
		runStatus: 'pr_created',
	});
	await routeIssueToPatchApprovalOverview(page, [fixture]);

	// Landing directly on the deep link opens the run inspector on load — no clicking required.
	await page.goto(`/#workflows?run=${fixture.workflowRun.id}`);
	await expect(page.getByRole('heading', { name: 'Run inspector' })).toBeVisible();
	await page.getByRole('tab', { name: 'Timeline' }).click();
	await expect(page.getByRole('list', { name: 'Workflow timeline' })).toBeVisible();
});

test('Workflow inspector retries a job with a mandatory reason', async ({ page }) => {
	const project = await getActiveProject(page);
	const fixture = issueToPatchApprovalFixture(project.id, 'queue', { completeEvidence: true });
	let retryCalled = false;
	await routeIssueToPatchApprovalOverview(page, [fixture]);
	await page.route(`/api/v1/jobs/${fixture.job.id}/retry`, async (route) => {
		retryCalled = true;
		const body = route.request().postDataJSON();
		expect(body.reason).toBe('Re-run the stuck QA lease.');
		await route.fulfill({ status: 202, json: { job: { ...fixture.job, status: 'queued' } } });
	});
	await page.goto('/#workflows');
	await page.getByRole('button', { name: `Inspect workflow ${fixture.workflow.title}` }).click();
	await expect(page.getByRole('heading', { name: 'Run inspector' })).toBeVisible();
	const retry = page.getByRole('button', { name: `Retry job ${fixture.job.kind}` }).first();
	await expect(retry).toBeVisible();
	await expect(retry).toBeDisabled();
	await page.getByLabel('Queue change reason').fill('Re-run the stuck QA lease.');
	await expect(retry).toBeEnabled();
	await retry.click();
	await expect.poll(() => retryCalled).toBe(true);
});

test('Workflow inspector cancels a job with a mandatory reason', async ({ page }) => {
	const project = await getActiveProject(page);
	const fixture = issueToPatchApprovalFixture(project.id, 'queue-cancel', { completeEvidence: true });
	let cancelCalled = false;
	await routeIssueToPatchApprovalOverview(page, [fixture]);
	await page.route(`/api/v1/jobs/${fixture.job.id}/cancel`, async (route) => {
		cancelCalled = true;
		const body = route.request().postDataJSON();
		expect(body.reason).toBe('Abort the superseded QA lease.');
		await route.fulfill({ status: 202, json: { job: { ...fixture.job, status: 'cancelled' } } });
	});
	await page.goto('/#workflows');
	await page.getByRole('button', { name: `Inspect workflow ${fixture.workflow.title}` }).click();
	await expect(page.getByRole('heading', { name: 'Run inspector' })).toBeVisible();
	const cancel = page.getByRole('button', { name: `Cancel job ${fixture.job.kind}` }).first();
	await expect(cancel).toBeVisible();
	await expect(cancel).toBeDisabled();
	await page.getByLabel('Queue change reason').fill('Abort the superseded QA lease.');
	await expect(cancel).toBeEnabled();
	await cancel.click();
	await expect.poll(() => cancelCalled).toBe(true);
});

test('Workflow timeline shows approval promotion and PR operational states', async ({ page }) => {
	const project = await getActiveProject(page);
	const fixture = issueToPatchApprovalFixture(project.id, 'timeline', { completeEvidence: true, runStatus: 'pr_created' });
	const now = new Date().toISOString();
	fixture.auditEvents = [
		{ id: 'audit-approved-timeline', projectId: project.id, action: 'workflow.issue_to_patch.approved_for_integration', actor: 'operator', target: fixture.workflowRun.id, payload: { reason: 'Approved after full diff and QA evidence review.' }, createdAt: now },
		{ id: 'audit-promoted-timeline', projectId: project.id, action: 'workflow.issue_to_patch.promoted_to_branch', actor: 'operator', target: fixture.workflowRun.id, payload: { reason: 'Promoted to a verified local branch.', branchName: 'aido/promote/ui-timeline' }, createdAt: now },
		{ id: 'audit-pr-timeline', projectId: project.id, action: 'workflow.issue_to_patch.pr_created', actor: 'operator', target: fixture.workflowRun.id, payload: { reason: 'GitHub returned HTTP 201 for the promoted branch.', pullRequest: { htmlUrl: 'https://github.test/aido/pulls/42' } }, createdAt: now },
	];
	await routeIssueToPatchApprovalOverview(page, [fixture]);

	await page.goto('/#workflows');
	await page.getByRole('button', { name: `Inspect workflow ${fixture.workflow.title}` }).click();
	await expect(page.getByRole('heading', { name: 'Run inspector' })).toBeVisible();
	await page.getByRole('tab', { name: 'Timeline' }).click();

	const timeline = page.getByRole('list', { name: 'Workflow timeline' });
	await expect(timeline).toContainText('workflow.issue_to_patch.approved_for_integration');
	await expect(timeline).toContainText('Approved after full diff and QA evidence review.');
	await expect(timeline).toContainText('workflow.issue_to_patch.promoted_to_branch');
	await expect(timeline).toContainText('Promoted to a verified local branch.');
	await expect(timeline).toContainText('workflow.issue_to_patch.pr_created');
	await expect(timeline).toContainText('GitHub returned HTTP 201 for the promoted branch.');
});

test('Workflow inspector exposes auditable real workflow detail without SQLite', async ({ page }) => {
	const project = await getActiveProject(page);
	const incomplete = issueToPatchApprovalFixture(project.id, 'detail-blocked', { completeEvidence: false, runStatus: 'blocked' });
	const complete = issueToPatchApprovalFixture(project.id, 'detail-pr', { completeEvidence: true, runStatus: 'pr_created' });
	complete.auditEvents = [
		{ id: 'audit-detail-pr', projectId: project.id, action: 'workflow.issue_to_patch.pr_created', actor: 'operator', target: complete.workflowRun.id, payload: { reason: 'GitHub returned HTTP 201.', pullRequest: { htmlUrl: 'https://github.test/aido/pulls/detail-pr' } }, createdAt: new Date().toISOString() },
	];
	await routeIssueToPatchApprovalOverview(page, [incomplete, complete]);

	await page.goto('/#workflows');
	await page.getByRole('button', { name: `Inspect workflow ${incomplete.workflow.title}` }).click();

	await expect(page.getByRole('heading', { name: 'Run inspector' })).toBeVisible();
	// Overview tab (default): completion gaps, blockers, jobs/leases and links.
	await expect(page.getByRole('heading', { name: 'What is missing for completed' })).toBeVisible();
	await expect(page.getByText('No diff evidence refs recorded.')).toBeVisible();
	await expect(page.getByText('No passing QA test results recorded.')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Blockers' })).toBeVisible();
	await expect(page.getByText('QA evidence is missing.').first()).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Jobs and leases' })).toBeVisible();
	await expect(page.getByText('worker-issue-to-patch')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Links' })).toBeVisible();
	await expect(page.getByText('No diff artifact link recorded.')).toBeVisible();
	// Model calls live in the Agents tab.
	await page.getByRole('tab', { name: 'Agents' }).click();
	await expect(page.getByRole('heading', { name: 'Model calls' })).toBeVisible();
	await expect(page.getByText('gpt-audit').first()).toBeVisible();

	await page.getByRole('button', { name: `Inspect workflow ${complete.workflow.title}` }).click();
	await expect(page.getByRole('heading', { name: 'Run inspector' })).toBeVisible();
	await expect(page.getByText('Completion prerequisites satisfied by linked records.')).toBeVisible();
	await expect(page.getByRole('link', { name: `Evidence ${complete.evidencePackage.id}` })).toBeVisible();
	await expect(page.getByRole('link', { name: 'PR https://github.test/aido/pulls/detail-pr' })).toBeVisible();
	// The artifact preview action lives in the Artifacts tab.
	await page.getByRole('tab', { name: 'Artifacts' }).click();
	await expect(page.getByRole('button', { name: 'Preview workflow artifact diff.patch' })).toBeVisible();
});

test('Evidence and QA shows persisted test result records', async ({ page }) => {
	await createWorkflowEvidence(page);
	await page.goto('/#evidence');

	await expect(page.getByRole('heading', { name: 'Evidence & QA' })).toBeVisible();
	await expect(page.getByText('uv run pytest tests_py -q').first()).toBeVisible();
	await expect(page.locator('.content-frame').getByText('passed').first()).toBeVisible();
});

test('Evidence and QA previews token-protected artifacts', async ({ page }) => {
	const workflow = await createWorkflowEvidence(page);
	await page.goto('/#evidence');

	await expect(page.getByText(workflow.artifactName).first()).toBeVisible();
	await page.getByRole('button', { name: `Preview artifact ${workflow.artifactName}` }).click();
	await expect(page.getByRole('dialog', { name: 'Artifact preview' })).toBeVisible();
	await expect(page.getByText('Workflow inspector artifact smoke.')).toBeVisible();
	await expect(page.getByText('text/markdown')).toBeVisible();
	await expect(page.getByText(/sha256 [a-f0-9]{12,}/)).toBeVisible();
	await expect(page.getByRole('button', { name: `Download artifact ${workflow.artifactName}` })).toBeVisible();
});

test('Evidence and QA renders auditable evidence detail with diff and redacted metadata', async ({ page }) => {
	const evidence = await createAuditableEvidence(page);
	await page.goto('/#evidence');

	await page.getByRole('button', { name: `View evidence package ${evidence.evidenceId}` }).click();

	await expect(page.getByRole('heading', { name: 'Evidence detail' })).toBeVisible();
	await expect(page.getByText(evidence.evidenceId).first()).toBeVisible();
	await expect(page.getByRole('link', { name: `Workflow ${evidence.workflowRunId}` })).toBeVisible();
	await expect(page.getByRole('link', { name: `Job ${evidence.jobId}` })).toBeVisible();
	await expect(page.getByRole('link', { name: `Agent run ${evidence.agentRunId}` })).toBeVisible();
	await expect(page.getByText(evidence.workspaceId).first()).toBeVisible();
	await expect(page.locator('.content-frame').getByText('failed').first()).toBeVisible();
	await expect(page.getByText(evidence.patchHash).first()).toBeVisible();
	await expect(page.getByText('diff.patch').first()).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Diff viewer' })).toBeVisible();
	await expect(page.getByText('diff --git a/local-control-center/web/src/features/pages.tsx')).toBeVisible();
	await expect(page.getByText('+export function EvidencePage({ token }) {')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Security findings' })).toBeVisible();
	await expect(page.getByText('hardcoded secret')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Model and tool calls' })).toBeVisible();
	await expect(page.getByText('gpt-audit')).toBeVisible();
	await expect(page.getByText('shell')).toBeVisible();
	await expect(page.getByText('[redacted]').first()).toBeVisible();
	await expect(page.locator('body')).not.toContainText(evidence.secret);
	await expect(page.locator('body')).not.toContainText(evidence.bearer);
	await expect(page.locator('body')).not.toContainText(evidence.passwordSecret);
	await expect(page.locator('body')).not.toContainText(evidence.clientSecret);
	await expect(page.locator('body')).not.toContainText(evidence.privateKey);
	await expect(page.locator('body')).not.toContainText(evidence.envApiKey);
	await expect(page.getByRole('button', { name: 'Download artifact diff.patch' }).first()).toBeVisible();
});

test('Evidence and QA marks empty patch artifacts as no real changes', async ({ page }) => {
	const evidence = await createAuditableEvidence(page, { emptyPatch: true });
	await page.goto('/#evidence');

	await page.getByRole('button', { name: `View evidence package ${evidence.evidenceId}` }).click();

	await expect(page.getByRole('heading', { name: 'Diff viewer' })).toBeVisible();
	await expect(page.getByText('Patch artifact is empty; no changes are proven.')).toBeVisible();
	await expect(page.getByText('no real changes')).toBeVisible();
	await expect(page.getByText('changed files 0').first()).toBeVisible();
});

test('Evidence and QA does not treat malformed patch lines as real changes', async ({ page }) => {
	const evidence = await createAuditableEvidence(page, { malformedPatch: true });
	await page.goto('/#evidence');

	await page.getByRole('button', { name: `View evidence package ${evidence.evidenceId}` }).click();

	await expect(page.getByRole('heading', { name: 'Diff viewer' })).toBeVisible();
	await expect(page.getByText('+export function EvidencePage({ token }) {')).toBeVisible();
	await expect(page.getByText('no real changes')).toBeVisible();
	await expect(page.getByText('real changes', { exact: true })).toHaveCount(0);
});

test('Governance shows architecture decisions, risks and next steps', async ({ page }) => {
	const governance = await createGovernanceState(page);
	await page.goto('/#governance');

	await expect(page.getByRole('heading', { name: 'Governance', exact: true })).toBeVisible();
	await expect(page.getByText(governance.decisionTitle)).toBeVisible();
	await expect(page.getByRole('cell', { name: governance.riskTitle })).toBeVisible();
	await expect(page.getByText(governance.nextStepTitle)).toBeVisible();
});

test('Governance filters records and updates risk status through strict controls', async ({ page }) => {
	const governance = await createGovernanceState(page);
	await page.goto('/#governance');

	await page.getByLabel('Governance filter').fill(governance.riskTitle);
	await expect(page.getByRole('cell', { name: governance.riskTitle })).toBeVisible();
	await page.getByLabel('Risk status filter').selectOption('open');
	await expect(page.getByRole('cell', { name: governance.riskTitle })).toBeVisible();

	await page.getByLabel('Risk to update').selectOption({ label: governance.riskTitle });
	await page.getByLabel('Risk update status').selectOption('mitigating');
	await page.getByRole('button', { name: 'Update risk status' }).click();
	await page.getByLabel('Risk status filter').selectOption('mitigating');
	await expect(page.getByRole('cell', { name: governance.riskTitle })).toBeVisible();
	await expect(page.getByRole('cell', { name: 'mitigating' }).first()).toBeVisible();
});

test('Policy & Security exposes tool-call execution state', async ({ page }) => {
	await createRuntimeTrace(page);
	await page.goto('/#policy');

	await expect(page.getByRole('heading', { name: 'Policy & Security' })).toBeVisible();
	await expect(page.getByText('Tool-call execution')).toBeVisible();
	await expect(page.getByText('python --version').first()).toBeVisible();
	await expect(page.getByText('not_executed').first()).toBeVisible();
});

test('Policy & Security shows sandbox policy revision diffs', async ({ page }) => {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	await page.request.patch('/api/v1/sandbox/profiles/default_docker', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			reason: 'Set baseline before visual diff smoke.',
			allowedImages: ['python:3.12-slim'],
			allowedNetworks: ['none'],
			defaultNetwork: 'none',
			memory: '1g',
			cpus: '1',
			timeoutSeconds: 120,
			status: 'active',
		},
	});

	await page.goto('/#policy');
	await page.getByLabel('Sandbox update reason').fill('Create visual policy diff smoke.');
	await page.getByLabel('Sandbox memory limit').fill('768m');
	await page.getByLabel('Sandbox CPU limit').fill('1');
	await page.getByLabel('Sandbox timeout seconds').fill('90');
	await page.getByLabel('Sandbox allowed image').fill('python:3.12-slim');
	await page.getByRole('button', { name: 'Save sandbox profile' }).click();

	await expect(page.getByText('Policy revisions')).toBeVisible();
	const revisionRow = page.getByRole('row', { name: /default_docker v\d+ memory, timeoutSeconds/ }).first();
	await expect(revisionRow).toBeVisible();
	await revisionRow.getByRole('button', { name: 'View policy revision diff for default_docker' }).click();
	const diffDialog = page.getByRole('dialog', { name: 'Policy revision diff' });
	await expect(diffDialog).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: 'memory' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: '"1g"' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: '"768m"' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: 'timeoutSeconds' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: '120' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: '90' })).toBeVisible();
});

test('Model Gateway renders model calls and cost ledger without synthetic runtime traces', async ({ page }) => {
	await createModelGatewayTrace(page);
	await page.goto('/#models');

	await expect(page.getByRole('heading', { name: 'Model Gateway', exact: true })).toBeVisible();
	await page.getByRole('tab', { name: 'Usage' }).click();
	await expect(page.getByRole('heading', { name: 'Model calls' })).toBeVisible();
	await expect(page.getByText('Cost history')).toBeVisible();
	await expect(page.locator('body')).not.toContainText('internal_mock');
});

test('Model Gateway console renders provider catalog routing usage budgets and CLI sessions', async ({ page }) => {
	await page.goto('/#models');

	await expect(page.getByRole('heading', { name: 'Model Gateway', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
	// Providers is the default landing tab; Settings lives there too.
	await expect(page.getByRole('heading', { name: 'Provider Accounts' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
	// Each group is reachable through its own tab (data loads on demand).
	await page.getByRole('tab', { name: 'Catalog' }).click();
	await expect(page.getByRole('heading', { name: 'Model Catalog' })).toBeVisible();
	await page.getByRole('tab', { name: 'Routing' }).click();
	await expect(page.getByRole('heading', { name: 'Routing Profiles' })).toBeVisible();
	await page.getByRole('tab', { name: 'Policies' }).click();
	await expect(page.getByRole('heading', { name: 'Role Assignments' })).toBeVisible();
	await page.getByRole('tab', { name: 'Budgets' }).click();
	await expect(page.getByRole('heading', { name: 'Budgets' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Provider Limits' })).toBeVisible();
	await page.getByRole('tab', { name: 'Usage' }).click();
	await expect(page.getByRole('heading', { name: 'Usage history' })).toBeVisible();
	await expect(page.getByText('No usage history entries')).toBeVisible();
	await page.getByRole('tab', { name: 'Decisions' }).click();
	await expect(page.getByRole('heading', { name: 'Routing Decisions' })).toBeVisible();
	await page.getByRole('tab', { name: 'CLI sessions' }).click();
	await expect(page.getByRole('heading', { name: 'CLI Sessions' })).toBeVisible();
	await expect(page.getByText('No CLI sessions')).toBeVisible();
	await page.getByRole('tab', { name: 'Benchmarks' }).click();
	await expect(page.getByRole('heading', { name: 'Benchmarks' })).toBeVisible();
	// Cold-loading the Benchmarks tab also pulls its shared provider/model dependencies.
	await expect(page.getByLabel('Benchmark provider')).toBeVisible();
	await page.getByLabel('Benchmark provider').selectOption('codex_cli');
	await page.getByLabel('Benchmark model').selectOption('gpt-5.5');
	await page.getByLabel('Benchmark runtime').selectOption('cli');
	await page.getByLabel('Benchmark role').selectOption('developer');
	await page.getByLabel('Benchmark cost USD').fill('0.42');
	await page.getByLabel('Benchmark latency ms').fill('1200');
	await page.getByRole('button', { name: 'Record benchmark outcome' }).click();
	await expect(page.getByText('Manual/operator-reported').first()).toBeVisible();
	await expect(page.getByText('Manual reports are not objective proof').first()).toBeVisible();
	await expect(page.getByText('0 objective / 1 manual').first()).toBeVisible();
	await expect(page.getByText('operator_reported').first()).toBeVisible();
	await expect(page.getByText('100.00%')).toHaveCount(0);
	await expect(page.getByRole('cell', { name: '$0.4200' }).first()).toBeVisible();
});

test('Model Gateway first load fetches only the active tab, not every endpoint', async ({ page }) => {
	let usageRequested = false;
	let decisionsRequested = false;
	let benchmarksRequested = false;
	await page.route('**/api/v1/model-gateway/usage-ledger', async (route) => {
		usageRequested = true;
		await route.continue();
	});
	await page.route('**/api/v1/model-gateway/routing-decisions', async (route) => {
		decisionsRequested = true;
		await route.continue();
	});
	await page.route('**/api/v1/model-gateway/benchmarks', async (route) => {
		benchmarksRequested = true;
		await route.continue();
	});
	await page.goto('/#models');
	// Default providers tab paints without touching the usage, decisions or benchmark endpoints.
	await expect(page.getByRole('heading', { name: 'Provider Accounts' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Runtime Providers' })).toBeVisible();
	// Deterministic load signal: the active (providers) tab is fully loaded once its rows render
	// (a seeded provider row appears). At that point any on-load lazy fetch would already have
	// fired, so the negative assertions below are reliable — without `networkidle`, which never
	// settles given the control plane's ~5s polling and 60s-times-out on Windows.
	await expect(page.getByText('anthropic_api').first()).toBeVisible();
	expect(usageRequested).toBe(false);
	expect(decisionsRequested).toBe(false);
	expect(benchmarksRequested).toBe(false);
	// The Usage endpoint is requested only when its tab is opened.
	await page.getByRole('tab', { name: 'Usage' }).click();
	await expect.poll(() => usageRequested).toBe(true);
	expect(decisionsRequested).toBe(false);
});

test('Model Gateway runtime table keeps a narrow primary view with a column chooser', async ({ page }) => {
	await page.goto('/#models');
	await expect(page.getByRole('heading', { name: 'Runtime Providers' })).toBeVisible();
	// Advanced columns are hidden from the primary view by default.
	await expect(page.getByRole('columnheader', { name: 'Version', exact: true })).toHaveCount(0);
	await expect(page.getByRole('columnheader', { name: 'Detected command', exact: true })).toHaveCount(0);
	// The column chooser reveals an advanced column on demand.
	await page.getByText('Columns', { exact: true }).click();
	await page.getByRole('checkbox', { name: 'Version' }).check();
	await expect(page.getByRole('columnheader', { name: 'Version', exact: true })).toBeVisible();
});

test('Runtime provider tables report unavailable states honestly', async ({ page }) => {
	const providersResponse = await page.request.get('/api/v1/runtime/providers');
	const runtimeStatus = await providersResponse.json();
	expect(runtimeStatus.runtimeModes).not.toContain('internal_mock');
	expect(runtimeStatus.providers.some((provider) => provider.id === 'internal_mock')).toBe(false);
	expect(runtimeStatus.providers.some((provider) => provider.available === false || provider.executable === false)).toBe(true);

	await page.goto('/#models');
	await expect(page.getByRole('heading', { name: 'Runtime Providers' })).toBeVisible();
	await expect(page.getByText('not executable').first()).toBeVisible();

	await page.goto('/#agents');
	await expect(page.getByRole('heading', { name: 'Runtime detection' })).toBeVisible();
	await expect(page.getByText('not executable').first()).toBeVisible();
});

function runtimeProvidersFixture(providers) {
	return {
		runtimeModes: ['api', 'cli', 'ollama', 'hybrid', 'manual'],
		providers,
		api: { available: false, configured: false, executable: false, reason: 'No API runtime configured.' },
		cli: { available: false, configured: false, executable: false, reason: 'No executable CLI runtime configured.' },
		ollama: { available: false, configured: false, executable: false, models: [], reason: 'Ollama endpoint is not configured.' },
		developerAgent: { available: false, configured: false, executable: false, reason: 'Developer agent runtime is not configured.' },
	};
}

function runtimeProviderConfigurationFixture(providers) {
	return { providers };
}

test('Runtime & Model Gateway shows missing config without marking provider ready', async ({ page }) => {
	await page.route('/api/v1/runtime/providers', async (route) => {
		await route.fulfill({
			json: runtimeProvidersFixture([
				{
					id: 'codex_cli',
					displayName: 'Codex CLI',
					kind: 'cli',
					configured: false,
					available: false,
					executable: false,
					detected: false,
					reason: 'Missing required runtime configuration: command.',
					capabilities: ['issue_to_patch', 'code_edit'],
					requiredConfiguration: ['command'],
					version: null,
					detectedCommand: null,
				},
			]),
		});
	});
	await page.route('/api/v1/runtime/provider-configuration', async (route) => {
		await route.fulfill({
			json: runtimeProviderConfigurationFixture([
				{
					id: 'codex_cli',
					kind: 'cli',
					configured: false,
					status: 'missing_config',
					missing: ['AIDO_CODEX_COMMAND'],
					reason: 'Missing required runtime configuration: AIDO_CODEX_COMMAND.',
					variables: [{ name: 'AIDO_CODEX_COMMAND', configured: false, fingerprint: null }],
				},
			]),
		});
	});
	await page.goto('/#models');

	await expect(page.getByRole('heading', { name: 'Runtime & Model Gateway' })).toBeVisible();
	const row = page.getByRole('row', { name: /codex_cli/ }).first();
	await expect(row).toContainText('missing_config');
	await expect(row).toContainText('AIDO_CODEX_COMMAND');
	await expect(row).toContainText('not configured');
	await expect(row).toContainText('not available');
	await expect(row).toContainText('not executable');
	await expect(row).not.toContainText('Ready');
});

test('Runtime & Model Gateway shows CLI not detected as separate state', async ({ page }) => {
	await page.route('/api/v1/runtime/providers', async (route) => {
		await route.fulfill({
			json: runtimeProvidersFixture([
				{
					id: 'codex_cli',
					displayName: 'Codex CLI',
					kind: 'cli',
					configured: true,
					available: false,
					executable: false,
					detected: false,
					reason: 'CLI runtime was not detected.',
					capabilities: ['issue_to_patch'],
					requiredConfiguration: ['command'],
					version: null,
					detectedCommand: null,
				},
			]),
		});
	});
	await page.goto('/#models');

	await expect(page.getByRole('columnheader', { name: 'Detected', exact: true })).toBeVisible();
	await expect(page.getByRole('columnheader', { name: 'Configured', exact: true })).toBeVisible();
	await expect(page.getByRole('columnheader', { name: 'Available', exact: true })).toBeVisible();
	await expect(page.getByRole('columnheader', { name: 'Executable', exact: true })).toBeVisible();
	const row = page.getByRole('row', { name: /codex_cli/ }).first();
	await expect(row).toContainText('not detected');
	await expect(row).toContainText('configured');
	await expect(row).toContainText('not available');
	await expect(row).toContainText('not executable');
});

test('Runtime & Model Gateway does not render provider secrets', async ({ page }) => {
	await page.route('/api/v1/runtime/providers', async (route) => {
		await route.fulfill({
			json: runtimeProvidersFixture([
				{
					id: 'openai_compatible',
					displayName: 'OpenAI Compatible',
					kind: 'api',
					configured: false,
					available: false,
					executable: false,
					detected: false,
					reason: 'Provider credential is missing for sk-web-secret-runtime-123456.',
					capabilities: ['chat'],
					requiredConfiguration: ['baseUrl', 'apiKey', 'model'],
					version: null,
					detectedCommand: null,
				},
			]),
		});
	});
	await page.route('/api/v1/runtime/provider-configuration', async (route) => {
		await route.fulfill({
			json: runtimeProviderConfigurationFixture([
				{
					id: 'openai_compatible',
					kind: 'api',
					configured: false,
					status: 'missing_config',
					missing: ['AIDO_OPENAI_COMPATIBLE_API_KEY'],
					reason: 'API key is missing.',
					variables: [{ name: 'AIDO_OPENAI_COMPATIBLE_API_KEY', configured: true, fingerprint: 'sha256:abcd1234' }],
				},
			]),
		});
	});
	await page.goto('/#models');

	await expect(page.locator('body')).not.toContainText('sk-web-secret-runtime');
	await expect(page.locator('body')).not.toContainText('Bearer ');
	await expect(page.getByText('[redacted_secret]').first()).toBeVisible();
	await expect(page.getByText('AIDO_OPENAI_COMPATIBLE_API_KEY').first()).toBeVisible();
});

test('Runtime & Model Gateway refreshes CLI healthcheck through backend endpoint', async ({ page }) => {
	let detectCalled = false;
	await page.route('/api/v1/runtime/providers', async (route) => {
		await route.fulfill({
			json: runtimeProvidersFixture([
				{
					id: 'codex_cli',
					displayName: 'Codex CLI',
					kind: 'cli',
					configured: true,
					available: false,
					executable: false,
					detected: false,
					reason: 'CLI runtime was not detected.',
					capabilities: ['issue_to_patch'],
					requiredConfiguration: ['command'],
					version: null,
					detectedCommand: null,
				},
			]),
		});
	});
	await page.route('/api/v1/model-gateway/cli-runtimes/codex_cli/detect', async (route) => {
		detectCalled = true;
		await route.fulfill({ json: { detection: { runtime: 'codex_cli', status: 'missing', message: 'CLI runtime was not detected.' } } });
	});
	await page.goto('/#models');

	await page.getByRole('button', { name: 'Refresh healthcheck for codex_cli' }).click();
	await expect.poll(() => detectCalled).toBe(true);
});

test('Runtime settings shows each provider card with the reason it cannot execute', async ({ page }) => {
	const reason = 'Set AIDO_CODEX_COMMAND to enable the Codex CLI runtime.';
	await page.route('/api/v1/runtime/providers', async (route) => {
		await route.fulfill({
			json: runtimeProvidersFixture([
				{
					id: 'codex_cli',
					displayName: 'Codex CLI',
					kind: 'cli',
					configured: true,
					available: false,
					executable: false,
					detected: false,
					reason,
					capabilities: ['issue_to_patch', 'code_edit'],
					requiredConfiguration: ['AIDO_CODEX_COMMAND'],
					version: null,
					detectedCommand: null,
				},
			]),
		});
	});
	// #settings-runtime now opens the Settings modal at the Providers & CLI section.
	await page.goto('/#settings-runtime');
	await expectControlPlaneLoaded(page);
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	// Confirm the Providers & CLI section nav item is active/visible.
	await expect(dialog.getByRole('button', { name: 'Providers & CLI', exact: true })).toBeVisible();

	// The provider renders as a card stating exactly why it cannot execute.
	const card = page.locator('.card').filter({ hasText: 'Codex CLI' });
	await expect(card).toBeVisible();
	await expect(card.getByText(reason)).toBeVisible();
	await expect(page.getByRole('button', { name: 'Refresh health' })).toBeVisible();

	// Details disclose the command state and the no-secrets guarantee.
	await card.getByRole('button', { name: 'Configuration details' }).click();
	await expect(card.getByText('Not detected on PATH.', { exact: true })).toBeVisible();
	await expect(card.getByText('Secret values are never shown — only whether they are set and a short fingerprint.')).toBeVisible();
});

test('Runtime settings shows guided setup actions when no runtime is executable', async ({ page }) => {
	const providers = [
		['codex_cli', 'Codex CLI', 'cli', 'Codex CLI was not found on PATH.'],
		['claude_code_cli', 'Claude Code', 'cli', 'Claude Code CLI was not found on PATH.'],
		['ollama', 'Ollama', 'local', 'Ollama daemon is not reachable.'],
		['openai_compatible', 'OpenAI-compatible API', 'api', 'API key is missing.'],
		['openrouter', 'OpenRouter', 'gateway', 'OpenRouter API key is missing.'],
		['nvidia_nim', 'NVIDIA NIM', 'api', 'NVIDIA NIM API key is missing.'],
	].map(([id, displayName, kind, reason]) => ({
		id,
		displayName,
		kind,
		configured: false,
		available: false,
		executable: false,
		detected: false,
		reason,
		healthStatus: 'unknown',
		capabilities: [],
		requiredConfiguration: ['configuration'],
	}));
	await page.route('/api/v1/runtime/providers', async (route) => {
		await route.fulfill({ json: runtimeProvidersFixture(providers) });
	});
	await page.route('/api/v1/runtime/provider-configuration', async (route) => {
		await route.fulfill({
			json: runtimeProviderConfigurationFixture(
				providers.map((provider) => ({
					id: provider.id,
					displayName: provider.displayName,
					kind: provider.kind,
					configured: false,
					status: 'configuration_required',
					missing: ['configuration'],
					reason: provider.reason,
					variables: [],
				})),
			),
		});
	});

	await page.goto('/#settings-runtime');
	await expectControlPlaneLoaded(page);
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();

	await expect(dialog.getByText('No executable runtimes')).toBeVisible();
	for (const label of [
		'Configurar Codex CLI',
		'Configurar Claude Code',
		'Configurar Ollama',
		'Configurar API',
		'Configurar NVIDIA NIM',
	]) {
		await expect(dialog.getByRole('button', { name: label })).toBeVisible();
	}
	for (const provider of providers) {
		const card = dialog.locator('.card').filter({ hasText: provider.displayName });
		await expect(card).toContainText(provider.reason);
		await expect(card.getByRole('button', { name: /Configurar|Run health check/ })).toBeVisible();
	}
});

test('Workbench composer infers task type and hides direct issue_to_patch controls', async ({ page }) => {
	await page.route('/api/v1/runtime/providers', async (route) => {
		await route.fulfill({
			json: runtimeProvidersFixture([
				{
					id: 'codex_cli',
					displayName: 'Codex CLI',
					kind: 'cli',
					configured: true,
					available: true,
					executable: true,
					detected: true,
					reason: 'Codex CLI healthcheck passed.',
					capabilities: ['issue_to_patch', 'code_edit'],
					requiredConfiguration: [],
				},
			]),
		});
	});
	await page.goto('/#command');
	await expect(page.locator('.workbench-layout')).toBeVisible();
	await expect(page.getByRole('radiogroup', { name: 'Task intake mode' })).toHaveCount(0);
	for (const legacyMode of ['Fix bug', 'Add feature', 'Refactor', 'Write tests']) {
		await expect(page.getByRole('radio', { name: legacyMode })).toHaveCount(0);
	}
	for (const legacyControl of [
		'Advanced',
		'AIDO decides',
		'Review brief',
		'Approve backlog',
		'Start iteration',
	]) {
		await expect(page.getByRole('button', { name: legacyControl })).toHaveCount(0);
	}
	for (const hiddenKnob of ['Preferred runtime', 'QA preset', 'Maximum cost USD', 'Target path']) {
		await expect(page.getByLabel(hiddenKnob, { exact: true })).toHaveCount(0);
	}

	await expect(page.getByRole('radiogroup', { name: 'Workbench mode' })).toBeVisible();
	await expect(page.getByRole('radio', { name: 'Consultation' })).toBeVisible();
	await expect(page.getByRole('radio', { name: 'Agents / Loop' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Agents / Loop', exact: true })).toBeDisabled();
	await page.getByLabel('What should AIDO do?').fill('Change a small file through the autonomous Product Owner intake.');
	await expect(page.getByRole('button', { name: 'Agents / Loop', exact: true })).toBeEnabled();
});

test('Model Gateway route preview submits request without exposing credentials', async ({ page }) => {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	await page.request.patch('/api/v1/model-gateway/providers/nvidia_nim', {
		headers: { 'X-Local-Control-Token': token },
		data: { enabled: true, healthStatus: 'healthy', lastError: 'Authorization: Bearer sk-websecret123456' },
	});
	await page.goto('/#models');

	// Wait for the patched nvidia_nim provider row to render so the redaction assertion below is
	// meaningful (that row carries the lastError that embeds the secret). The credential shows as a
	// reference (e.g. `env:NVIDIA_NIM_API_KEY`), whose exact name is incidental and non-deterministic
	// across seeds — the invariant under test is that the secret value is never exposed verbatim.
	await expect(page.getByText('nvidia_nim').first()).toBeVisible();
	await expect(page.locator('body')).not.toContainText('sk-websecret');
	await page.getByRole('tab', { name: 'Routing' }).click();
	await page.getByLabel('Preview role').selectOption('analyst');
	await page.getByLabel('Preview mode').selectOption('free_first');
	await page.getByLabel('Preview task type').fill('research_brief');
	await page.getByLabel('Preview context tokens').fill('4000');
	await page.getByLabel('Preview requires search').check();
	await page.getByRole('button', { name: 'Preview route' }).click();

	await expect(page.getByText('Selected route')).toBeVisible();
	await expect(page.getByText('Budget result')).toBeVisible();
	await expect(page.getByText('Quota result')).toBeVisible();
	await expect(page.getByText('nvidia_nim').first()).toBeVisible();
	await expect(page.locator('body')).not.toContainText('sk-');
});

test('strict configuration forms prevent manual JSON edits', async ({ page }) => {
	await page.goto('/#agents');

	await expect(page.getByLabel('Profile id')).toBeVisible();
	await expect(page.locator('textarea[data-json-editor="true"]')).toHaveCount(0);
	await page.getByLabel('Profile id').fill('Bad Profile!');
	await page.getByRole('button', { name: 'Save agent profile' }).click();
	await expect(page.getByText('Use lowercase letters, numbers, dashes or underscores.')).toBeVisible();

	const suffix = Date.now();
	const profileId = `web_form_${suffix}`;
	const profileName = `Web Form Agent ${suffix}`;
	await page.getByLabel('Profile id').fill(profileId);
	await page.getByLabel('Display name').fill(profileName);
	await page.getByLabel('Role', { exact: true }).selectOption('developer');
	await page.getByLabel('Routing profile').selectOption('balanced_best_value');
	await page.getByLabel('Role model policy').selectOption('developer');
	await page.getByLabel('Runtime mode').selectOption('hybrid');
	await page.getByLabel('Permission profile').selectOption('dev_safe');
	await page.getByLabel('Allowed tool').selectOption('shell');
	await page.getByLabel('Allowed provider').selectOption('codex_cli');
	await page.getByLabel('Allowed runtime').selectOption('cli');
	await page.getByLabel('Max tokens per run').fill('120000');
	await page.getByLabel('Approval threshold USD').fill('1.25');
	await page.getByLabel('Allow API').uncheck();
	await page.getByRole('button', { name: 'Save agent profile' }).click();
	await expect(page.locator('td[data-label="Name"]').filter({ hasText: profileName })).toBeVisible();
	const profilesResponse = await page.request.get('/api/v1/agent-profiles');
	const profile = (await profilesResponse.json()).agentProfiles.find((item) => item.id === profileId);
	expect(profile.routingProfileId).toBe('balanced_best_value');
	expect(profile.roleModelPolicyId).toBe('developer');
	expect(profile.allowedProviders).toEqual(['codex_cli']);
	expect(profile.allowedRuntimes).toEqual(['cli']);
	expect(profile.maxTokensPerRun).toBe(120000);
	expect(profile.allowApi).toBe(false);
	expect(profile.requiresApprovalOverUsd).toBe(1.25);

	await ensureLocalModelGatewayCatalog(page);
	await page.goto('/#models');
	await page.getByRole('tab', { name: 'Policies' }).click();
	await expect(page.getByLabel('Policy id')).toBeVisible();
	await expect(page.locator('textarea[data-json-editor="true"]')).toHaveCount(0);
	await page.getByLabel('Policy id').fill('Bad Policy!');
	await page.getByRole('button', { name: 'Save model policy' }).click();
	await expect(page.getByText('Policy id must use lowercase letters, numbers, dashes or underscores.')).toBeVisible();

	const policyId = `web_policy_${suffix}`;
	await page.getByLabel('Policy id').fill(policyId);
	await page.getByLabel('Policy name').fill('Web Policy');
	await page.getByLabel('Preferred provider').selectOption('ollama');
	await page.getByLabel('Model', { exact: true }).selectOption('local_default');
	await page.getByLabel('Maximum cost USD').fill('1');
	await page.getByLabel('Maximum tokens').fill('4000');
	await page.getByRole('button', { name: 'Save model policy' }).click();
	await expect(page.getByRole('cell', { name: policyId })).toBeVisible();
});

test('strict operational forms cover workflows governance sandbox and MCP settings', async ({ page }) => {
	await page.goto('/#command');
	await expect(page.locator('.workbench-layout')).toBeVisible();

	await expect(page.getByLabel('Workspace folder', { exact: true })).toBeVisible();
	await expect(page.getByLabel('What should AIDO do?')).toBeVisible();
	await expect(page.getByRole('radiogroup', { name: 'Task intake mode' })).toHaveCount(0);
	await expect(page.getByRole('radio', { name: 'Fix bug' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Advanced' })).toHaveCount(0);
	await expect(page.getByRole('radiogroup', { name: 'Workbench mode' })).toBeVisible();
	await expect(page.getByRole('radio', { name: 'Consultation' })).toBeVisible();
	await expect(page.getByRole('radio', { name: 'Agents / Loop' })).toBeVisible();
	// Runtime/QA/cost are not selected manually from Workbench; intake policy resolves them server-side.
	await expect(page.getByLabel('Preferred runtime')).toHaveCount(0);
	await expect(page.getByLabel('QA preset')).toHaveCount(0);
	await expect(page.getByLabel('Maximum cost USD')).toHaveCount(0);
	await expect(page.locator('textarea[data-json-editor="true"]')).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Agents / Loop', exact: true })).toBeDisabled();

	const suffix = Date.now();

	await page.goto('/#governance');
	await expect(page.getByLabel('Risk title')).toBeVisible();
	await page.getByLabel('Risk title').fill(`High risk ${suffix}`);
	await page.getByLabel('Risk severity').selectOption('high');
	await page.getByRole('button', { name: 'Save risk' }).click();
	await expect(page.getByText('High and critical risks require mitigation.')).toBeVisible();
	await page.getByLabel('Risk mitigation').fill('Track owner, date and validation evidence before closing.');
	await page.getByRole('button', { name: 'Save risk' }).click();
	await expect(page.getByRole('cell', { name: `High risk ${suffix}` })).toBeVisible();

	await page.getByLabel('Decision title').fill(`Decision ${suffix}`);
	await page.getByLabel('Decision status').selectOption('accepted');
	await page.getByRole('button', { name: 'Save decision' }).click();
	await expect(page.getByText('Accepted decisions require context and decision text.')).toBeVisible();
	await page.getByLabel('Decision context').fill('Configuration must be edited through controlled forms.');
	await page.getByLabel('Decision text').fill('Keep JSON out of operator workflows.');
	await page.getByRole('button', { name: 'Save decision' }).click();
	await expect(page.getByRole('cell', { name: `Decision ${suffix}` })).toBeVisible();

	await page.getByLabel('Next step title').fill(`Next step ${suffix}`);
	await page.getByLabel('Next step priority').selectOption('urgent');
	await page.getByRole('button', { name: 'Save next step' }).click();
	await expect(page.getByRole('cell', { name: `Next step ${suffix}` })).toBeVisible();

	await page.goto('/#policy');
	await page.getByLabel('Sandbox update reason').fill('');
	await page.getByRole('button', { name: 'Save sandbox profile' }).click();
	await expect(page.getByText('Sandbox update reason is required.')).toBeVisible();
	await page.getByLabel('Sandbox update reason').fill('Constrain post-MVP smoke runtime resources.');
	await page.getByLabel('Sandbox memory limit').fill('768m');
	await page.getByLabel('Sandbox CPU limit').fill('1');
	await page.getByLabel('Sandbox timeout seconds').fill('120');
	await page.getByLabel('Sandbox allowed image').fill('python:3.12-slim');
	await page.getByRole('button', { name: 'Save sandbox profile' }).click();
	await expect(page.getByText('768m').first()).toBeVisible();

	await page.goto('/#integrations');
	await expect(page.getByLabel('MCP server id')).toBeVisible();
	await page.getByLabel('MCP server id').fill('Bad Server!');
	await page.getByRole('button', { name: 'Register MCP server' }).click();
	await expect(page.getByText('MCP server id must use lowercase letters, numbers, dashes or underscores.')).toBeVisible();
	const mcpId = `mcp_form_${suffix}`;
	await page.getByLabel('MCP server id').fill(mcpId);
	await page.getByLabel('MCP command').fill('python -m local_mcp_server');
	await page.getByLabel('MCP transport').selectOption('stdio');
	await page.getByRole('button', { name: 'Register MCP server' }).click();
	await expect(page.getByRole('cell', { name: mcpId })).toBeVisible();
});

test('command palette executes v1 actions and workflow inspector shows linked records', async ({ page }) => {
	const workflow = await createWorkflowEvidence(page);
	await createRuntimeTrace(page, workflow.workflowRunId);
	await createApprovalJob(page);

	await page.goto('/#workflows');
	await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();

	await page.getByRole('button', { name: `Inspect workflow ${workflow.title}` }).click();
	const inspector = page.getByRole('complementary', { name: 'Inspector' });
	await expect(page.getByRole('heading', { name: 'Run inspector' })).toBeVisible();
	await expect(inspector.getByText(workflow.title).first()).toBeVisible();
	// Timeline tab: merged steps + workflow events.
	await page.getByRole('tab', { name: 'Timeline' }).click();
	const workflowTimeline = page.getByLabel('Workflow timeline');
	await expect(workflowTimeline).toContainText('workspace_create');
	await expect(workflowTimeline).toContainText('workflow.started');
	// Evidence tab: steps and evidence packages.
	await page.getByRole('tab', { name: 'Evidence' }).click();
	await expect(inspector.getByText('workspace_create').first()).toBeVisible();
	await expect(inspector.getByText('web-story-evidence').first()).toBeVisible();
	// Agents tab: linked tool calls.
	await page.getByRole('tab', { name: 'Agents' }).click();
	await expect(inspector.getByText('python --version').first()).toBeVisible();
	// Policy tab: security & policy decisions.
	await page.getByRole('tab', { name: 'Policy' }).click();
	await expect(inspector.getByText('Security and policy decisions')).toBeVisible();
	await expect(inspector.getByText('allowlisted_diagnostic').first()).toBeVisible();
	// Artifacts tab: preview/download.
	await page.getByRole('tab', { name: 'Artifacts' }).click();
	await expect(inspector.getByRole('heading', { name: 'Artifacts' })).toBeVisible();
	await expect(inspector.getByText(workflow.artifactName).first()).toBeVisible();
	await page.getByRole('button', { name: `Preview workflow artifact ${workflow.artifactName}` }).click();
	await expect(page.getByRole('dialog', { name: 'Workflow artifact preview' })).toBeVisible();
	await expect(page.getByText('Workflow inspector artifact smoke.')).toBeVisible();
	await expect(page.getByRole('button', { name: `Download workflow artifact ${workflow.artifactName}` })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Workflow artifact preview' })).toBeHidden();

	await page.keyboard.press('Control+k');
	const approvalsPalette = page.getByRole('dialog', { name: 'Command palette' });
	await approvalsPalette.getByRole('combobox', { name: 'Filter commands' }).fill('approval');
	await approvalsPalette.getByRole('option', { name: /Review pending approvals/ }).click();
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeVisible();
});

// NOTE: 'command palette can create a workflow' was removed — the redesigned palette
// (commandActions.ts) intentionally dropped the Create Workflow action; the palette is
// covered by command-palette.spec.js. Restore the action + this test if the removal was unintended.
