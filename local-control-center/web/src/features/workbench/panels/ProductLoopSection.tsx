/**
 * Renders one product-loop section (questions, brief, assumptions, decisions, architecture, backlog,
 * tasks) from the live project-scoped product-loop endpoint, with loading, error and empty
 * states. Pure presentation: the page owns the fetch (useProductLoop) and passes the data down; the
 * architecture section reuses the already-available overview architecture decisions.
 * @author Rodrigo Mason
 */

import { CheckCircle2, PlayCircle, Sparkles } from 'lucide-react';
import type { ReactNode } from 'react';

import type { ProjectProductLoopResponse } from '../../../api/client';
import type { Overview } from '../../../api/types';
import { Disclosure } from '../../../components/Disclosure';
import {
	StatusChip as Badge,
	Button,
	EmptyState,
	ErrorState,
	Skeleton,
} from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { toneForStatus } from '../../../lib/format';
import { StorySpecDialog } from './StorySpecDialog';

/** The loop sections backed by the product-loop endpoint (or, for architecture, the overview). */
export type ProductLoopSectionId =
	| 'questions'
	| 'brief'
	| 'assumptions'
	| 'decisions'
	| 'architecture'
	| 'backlog';

type ProductLoopSectionProps = {
	section: ProductLoopSectionId;
	data: ProjectProductLoopResponse | null;
	architectureDecisions: Overview['architectureDecisions'];
	loading: boolean;
	error: string;
	actions?: {
		busy?: boolean;
		onAidoDecide?: () => void;
		onApproveBrief?: () => void;
		onApproveBacklog?: () => void;
		onStartIteration?: () => void;
		onExpandEpic?: (epicId: string) => void;
	};
};

type LoopState = NonNullable<ProjectProductLoopResponse>;
type AgentTask = LoopState['tasks'][number];
type LoopQuestion = LoopState['questions'][number];

/** A labelled value block; renders nothing when the value is empty so optional fields stay quiet. */
function Labeled({ label, children }: { label: string; children: ReactNode }) {
	return (
		<div className="stack compact">
			<span className="field-help">{label}</span>
			<div>{children}</div>
		</div>
	);
}

/** Renders a JSON string array as a comma-free bullet line; empty arrays render nothing upstream. */
function valueList(items: unknown[]): string {
	return items.map((item) => String(item)).join(' · ');
}

function metadataRecord(metadata: unknown): Record<string, unknown> {
	return metadata && typeof metadata === 'object' && !Array.isArray(metadata)
		? (metadata as Record<string, unknown>)
		: {};
}

function metadataList(metadata: unknown, key: string): string[] {
	const value = metadataRecord(metadata)[key];
	return Array.isArray(value)
		? value.map((item) => String(item).trim()).filter((item) => item.length > 0)
		: [];
}

function metadataText(metadata: unknown, key: string): string {
	const value = metadataRecord(metadata)[key];
	return typeof value === 'string' ? value.trim() : '';
}

/** One clarification question: its options, and — once resolved — the recorded answer, so an answered
 *  question is legible instead of an identical unanswered-looking card that invites a repeat. */
function QuestionCard({ question }: { question: LoopQuestion }) {
	const { t } = useI18n();
	const options = metadataList(question.metadata, 'options');
	const recommendation = metadataText(question.metadata, 'recommendation');
	const defaultDecision = metadataText(question.metadata, 'defaultDecision');
	const answer = metadataText(question.metadata, 'aidoDecision');
	const isChosen = (option: string): boolean =>
		answer ? option === answer : option === recommendation || option === defaultDecision;
	return (
		<article className="card">
			<div className="inline">
				<Badge tone={toneForStatus(question.status)}>{question.status}</Badge>
				<span className="muted">{question.priority}</span>
			</div>
			<p className="card-body">{question.question}</p>
			{answer ? (
				<Labeled label={t('app.workbench.loop.questions.answer', 'Answer')}>
					<Badge tone="ok">{answer}</Badge>
				</Labeled>
			) : null}
			{options.length ? (
				<Labeled label={t('app.workbench.loop.questions.options', 'Options')}>
					<div className="inline">
						{options.map((option) => (
							<Badge key={option} tone={isChosen(option) ? 'ok' : 'info'}>
								{option}
							</Badge>
						))}
					</div>
				</Labeled>
			) : null}
		</article>
	);
}

function tasksByAgent(
	data: LoopState,
	storyId: string,
): Array<{ label: string; tasks: AgentTask[] }> {
	const groups = new Map<string, { label: string; tasks: AgentTask[] }>();
	for (const task of data.tasks.filter((item) => item.storyId === storyId)) {
		const assignments = data.assignments.filter((assignment) => assignment.taskId === task.id);
		if (!assignments.length) {
			const key = `unassigned:${task.role}`;
			const group = groups.get(key) ?? { label: `Unassigned · ${task.role}`, tasks: [] };
			group.tasks.push(task);
			groups.set(key, group);
			continue;
		}
		for (const assignment of assignments) {
			const key = assignment.agentId;
			const group = groups.get(key) ?? {
				label: `${assignment.agentId} · ${assignment.role}`,
				tasks: [],
			};
			group.tasks.push(task);
			groups.set(key, group);
		}
	}
	return [...groups.values()];
}

/** Renders the active loop section from live data, with loading/error/empty fallbacks. */
export function ProductLoopSection({
	section,
	data,
	architectureDecisions,
	loading,
	error,
	actions,
}: ProductLoopSectionProps) {
	const { t } = useI18n();

	if (loading && !data) {
		return <Skeleton label={t('app.workbench.loop.loading', 'Loading the product loop')} />;
	}
	if (error) {
		return (
			<ErrorState
				title={t('app.workbench.loop.errorTitle', 'Could not load the product loop')}
				body={error}
			/>
		);
	}

	if (section === 'questions') {
		const questions = data?.questions ?? [];
		if (!questions.length) {
			return (
				<EmptyState
					title={t('app.workbench.loop.questions.emptyTitle', 'No pending questions')}
					body={t(
						'app.workbench.loop.questions.emptyBody',
						'AIDO surfaces impact questions here once discovery runs against this workspace.',
					)}
				/>
			);
		}
		const openQuestions = questions.filter((question) => question.status === 'open');
		const answeredQuestions = questions.filter((question) => question.status !== 'open');
		return (
			<div className="stack">
				<div className="inline">
					<Button
						disabled={!actions?.onAidoDecide || !openQuestions.length}
						icon={<Sparkles size={16} aria-hidden="true" />}
						loading={actions?.busy}
						onClick={actions?.onAidoDecide}
						variant="primary"
					>
						{t('app.workbench.loop.questions.aidoDecide', 'AIDO decide')}
					</Button>
				</div>
				{openQuestions.map((question) => (
					<QuestionCard key={question.id} question={question} />
				))}
				{answeredQuestions.length ? (
					<Disclosure
						headingLevel={4}
						title={t('app.workbench.loop.questions.answered', '{count} answered').replace(
							'{count}',
							String(answeredQuestions.length),
						)}
					>
						<div className="stack">
							{answeredQuestions.map((question) => (
								<QuestionCard key={question.id} question={question} />
							))}
						</div>
					</Disclosure>
				) : null}
			</div>
		);
	}

	if (section === 'brief') {
		const brief = data?.brief ?? null;
		if (!brief) {
			return (
				<EmptyState
					title={t('app.workbench.loop.brief.emptyTitle', 'No product brief yet')}
					body={t(
						'app.workbench.loop.brief.emptyBody',
						'The living product brief appears here once the product owner agent produces it.',
					)}
				/>
			);
		}
		return (
			<article className="card">
				<div className="inline">
					<Badge tone={toneForStatus(brief.status)}>{brief.status}</Badge>
					<strong className="card-title">{brief.title}</strong>
					<Button
						disabled={!actions?.onApproveBrief || brief.status === 'approved'}
						icon={<CheckCircle2 size={16} aria-hidden="true" />}
						loading={actions?.busy}
						onClick={actions?.onApproveBrief}
						variant="primary"
					>
						{t('app.workbench.loop.brief.approve', 'Approve brief')}
					</Button>
				</div>
				{brief.summary ? <p className="card-body">{brief.summary}</p> : null}
				{brief.problemStatement ? (
					<Labeled label={t('app.workbench.loop.brief.problem', 'Problem statement')}>
						{brief.problemStatement}
					</Labeled>
				) : null}
				{brief.goals.length ? (
					<Labeled label={t('app.workbench.loop.brief.goals', 'Goals')}>
						{valueList(brief.goals)}
					</Labeled>
				) : null}
				{brief.targetUsers.length ? (
					<Labeled label={t('app.workbench.loop.brief.targetUsers', 'Target users')}>
						{valueList(brief.targetUsers)}
					</Labeled>
				) : null}
				{brief.successMetrics.length ? (
					<Labeled label={t('app.workbench.loop.brief.successMetrics', 'Success metrics')}>
						{valueList(brief.successMetrics)}
					</Labeled>
				) : null}
				{brief.scope ? (
					<Labeled label={t('app.workbench.loop.brief.scope', 'Scope')}>{brief.scope}</Labeled>
				) : null}
				{brief.outOfScope ? (
					<Labeled label={t('app.workbench.loop.brief.outOfScope', 'Out of scope')}>
						{brief.outOfScope}
					</Labeled>
				) : null}
			</article>
		);
	}

	if (section === 'assumptions') {
		const assumptions = data?.assumptions ?? [];
		if (!assumptions.length) {
			return (
				<EmptyState
					title={t('app.workbench.loop.assumptions.emptyTitle', 'No assumptions recorded')}
					body={t(
						'app.workbench.loop.assumptions.emptyBody',
						'Assumptions captured during discovery are listed here with their rationale.',
					)}
				/>
			);
		}
		return (
			<div className="stack">
				{assumptions.map((assumption) => (
					<article className="card" key={assumption.id}>
						<div className="inline">
							<Badge tone={toneForStatus(assumption.status)}>{assumption.status}</Badge>
							{assumption.confidence ? (
								<span className="muted">
									{t('app.workbench.loop.assumptions.confidence', 'Confidence')}:{' '}
									{assumption.confidence}
								</span>
							) : null}
						</div>
						<p className="card-body">{assumption.statement}</p>
					</article>
				))}
			</div>
		);
	}

	if (section === 'decisions') {
		const decisions = data?.decisions ?? [];
		if (!decisions.length) {
			return (
				<EmptyState
					title={t('app.workbench.loop.decisions.emptyTitle', 'No decisions yet')}
					body={t(
						'app.workbench.loop.decisions.emptyBody',
						'Product and architecture decisions, with alternatives and reversibility, appear here.',
					)}
				/>
			);
		}
		return (
			<div className="stack">
				{decisions.map((decision) => (
					<article className="card" key={decision.id}>
						<div className="inline">
							<Badge tone={toneForStatus(decision.status)}>{decision.status}</Badge>
							<strong className="card-title">{decision.title}</strong>
						</div>
						{decision.decision ? <p className="card-body">{decision.decision}</p> : null}
						{decision.rationale ? (
							<Labeled label={t('app.workbench.loop.decisions.rationale', 'Rationale')}>
								{decision.rationale}
							</Labeled>
						) : null}
						{decision.consequences.length ? (
							<Labeled label={t('app.workbench.loop.decisions.consequences', 'Consequences')}>
								{valueList(decision.consequences)}
							</Labeled>
						) : null}
					</article>
				))}
			</div>
		);
	}

	if (section === 'architecture') {
		if (!architectureDecisions.length) {
			return (
				<EmptyState
					title={t('app.workbench.loop.architecture.emptyTitle', 'No architecture options yet')}
					body={t(
						'app.workbench.loop.architecture.emptyBody',
						'Candidate architecture options and their tradeoffs are compared here.',
					)}
				/>
			);
		}
		return (
			<div className="stack">
				{architectureDecisions.map((decision) => (
					<article className="card" key={decision.id}>
						<div className="inline">
							<Badge tone={toneForStatus(decision.status)}>{decision.status}</Badge>
							<strong className="card-title">{decision.title}</strong>
						</div>
						{decision.decision ? <p className="card-body">{decision.decision}</p> : null}
						{decision.context ? (
							<Labeled label={t('app.workbench.loop.brief.problem', 'Problem statement')}>
								{decision.context}
							</Labeled>
						) : null}
					</article>
				))}
			</div>
		);
	}

	if (section === 'backlog') {
		const epics = data?.epics ?? [];
		const stories = data?.stories ?? [];
		const acceptanceCriteria = data?.acceptanceCriteria ?? [];
		const tasks = data?.tasks ?? [];
		if (!epics.length && !stories.length && !tasks.length) {
			return (
				<EmptyState
					title={t('app.workbench.loop.backlog.emptyTitle', 'No backlog yet')}
					body={t(
						'app.workbench.loop.backlog.emptyBody',
						'Epics, user stories and acceptance criteria appear here once the backlog is generated.',
					)}
				/>
			);
		}
		return (
			<div className="stack">
				<div className="inline">
					<Button
						disabled={!actions?.onApproveBacklog || !stories.length}
						icon={<CheckCircle2 size={16} aria-hidden="true" />}
						loading={actions?.busy}
						onClick={actions?.onApproveBacklog}
						variant="primary"
					>
						{t('app.workbench.loop.backlog.approve', 'Approve backlog')}
					</Button>
					<Button
						disabled={!actions?.onStartIteration || !stories.length}
						icon={<PlayCircle size={16} aria-hidden="true" />}
						loading={actions?.busy}
						onClick={actions?.onStartIteration}
					>
						{t('app.workbench.loop.backlog.startIteration', 'Start iteration')}
					</Button>
				</div>
				{epics.length ? (
					<Labeled label={t('app.workbench.loop.backlog.epics', 'Epics')}>
						<div className="stack compact">
							{epics.map((epic) => (
								<div className="inline" key={epic.id}>
									<Badge tone={toneForStatus(epic.status)}>{epic.status}</Badge>
									<span>{epic.title}</span>
									<span className="muted">
										{stories.filter((story) => story.epicId === epic.id).length}{' '}
										{t('app.workbench.loop.backlog.storyCount', 'stories')}
									</span>
									{actions?.onExpandEpic ? (
										<Button disabled={actions.busy} onClick={() => actions.onExpandEpic?.(epic.id)}>
											{t('app.workbench.loop.backlog.expandEpic', 'Expand epic')}
										</Button>
									) : null}
								</div>
							))}
						</div>
					</Labeled>
				) : null}
				{stories.length ? (
					<Labeled label={t('app.workbench.loop.backlog.stories', 'User stories')}>
						<div className="stack compact">
							{stories.map((story) => {
								const storyCriteria = acceptanceCriteria.filter(
									(criterion) => criterion.storyId === story.id,
								);
								const agentGroups = data ? tasksByAgent(data, story.id) : [];
								return (
									<Disclosure
										key={story.id}
										title={story.title}
										summary={
											<span className="inline">
												<Badge tone={toneForStatus(story.status)}>{story.status}</Badge>
												<span className="muted">
													{storyCriteria.length}{' '}
													{t('app.workbench.loop.backlog.criteriaCount', 'criteria')}
												</span>
												<span className="muted">
													{agentGroups.reduce((total, group) => total + group.tasks.length, 0)}{' '}
													{t('app.workbench.loop.backlog.taskCount', 'tasks')}
												</span>
											</span>
										}
									>
										<div className="stack compact">
											{story.asA || story.iWant || story.soThat ? (
												<p className="card-body">
													{t('app.workbench.loop.story.asA', 'As a')} {story.asA};{' '}
													{t('app.workbench.loop.story.iWant', 'I want')} {story.iWant};{' '}
													{t('app.workbench.loop.story.soThat', 'so that')} {story.soThat}
												</p>
											) : null}
											<Labeled
												label={t(
													'app.workbench.loop.backlog.acceptanceCriteria',
													'Acceptance criteria',
												)}
											>
												<div className="stack compact">
													{storyCriteria.map((criterion) => (
														<div className="inline" key={criterion.id}>
															<Badge tone={toneForStatus(criterion.status)}>
																{criterion.status}
															</Badge>
															<span>{criterion.criterion}</span>
														</div>
													))}
												</div>
											</Labeled>
											<div className="inline">
												<StorySpecDialog
													projectId={story.projectId}
													storyId={story.id}
													storyTitle={story.title}
												/>
											</div>
											<Labeled
												label={t(
													'app.workbench.loop.backlog.agentTasksByAgent',
													'Agent tasks by agent',
												)}
											>
												<div className="stack compact">
													{agentGroups.length ? (
														agentGroups.map((group) => (
															<div className="stack compact" key={group.label}>
																<div className="inline">
																	<span className="mono">{group.label}</span>
																	<Badge>{group.tasks.length}</Badge>
																</div>
																{group.tasks.map((task) => (
																	<div className="inline" key={`${group.label}:${task.id}`}>
																		<Badge tone={toneForStatus(task.status)}>{task.status}</Badge>
																		<span className="mono">{task.role}</span>
																		<span>{task.title}</span>
																	</div>
																))}
															</div>
														))
													) : (
														<span className="muted">
															{t(
																'app.workbench.loop.backlog.noAgentTasks',
																'No technical tasks decomposed yet.',
															)}
														</span>
													)}
												</div>
											</Labeled>
										</div>
									</Disclosure>
								);
							})}
						</div>
					</Labeled>
				) : null}
			</div>
		);
	}

	return null;
}
