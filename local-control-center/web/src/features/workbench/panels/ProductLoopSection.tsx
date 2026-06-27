/**
 * Renders one product-loop section (questions, brief, assumptions, decisions, architecture, backlog,
 * iterations) from the live project-scoped product-loop endpoint, with loading, error and empty
 * states. Pure presentation: the page owns the fetch (useProductLoop) and passes the data down; the
 * architecture section reuses the already-available overview architecture decisions.
 * @author Rodrigo Mason
 */

import type { ReactNode } from 'react';

import type { ProjectProductLoopResponse } from '../../../api/client';
import type { Overview } from '../../../api/types';
import { Badge, EmptyState } from '../../../components/primitives';
import { ErrorState, Skeleton } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { toneForStatus } from '../../../lib/format';

/** The loop sections backed by the product-loop endpoint (or, for architecture, the overview). */
export type ProductLoopSectionId =
	| 'questions'
	| 'brief'
	| 'assumptions'
	| 'decisions'
	| 'architecture'
	| 'backlog'
	| 'iterations';

type ProductLoopSectionProps = {
	section: ProductLoopSectionId;
	data: ProjectProductLoopResponse | null;
	architectureDecisions: Overview['architectureDecisions'];
	loading: boolean;
	error: string;
};

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

/** Renders the active loop section from live data, with loading/error/empty fallbacks. */
export function ProductLoopSection({
	section,
	data,
	architectureDecisions,
	loading,
	error,
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
		return (
			<div className="stack">
				{questions.map((question) => (
					<article className="card" key={question.id}>
						<div className="inline">
							<Badge tone={toneForStatus(question.status)}>{question.status}</Badge>
							<span className="muted">{question.priority}</span>
						</div>
						<p className="card-body">{question.question}</p>
					</article>
				))}
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
				{epics.length ? (
					<Labeled label={t('app.workbench.loop.backlog.epics', 'Epics')}>
						<div className="stack compact">
							{epics.map((epic) => (
								<div className="inline" key={epic.id}>
									<Badge tone={toneForStatus(epic.status)}>{epic.status}</Badge>
									<span>{epic.title}</span>
								</div>
							))}
						</div>
					</Labeled>
				) : null}
				{stories.length ? (
					<Labeled label={t('app.workbench.loop.backlog.stories', 'User stories')}>
						<div className="stack compact">
							{stories.map((story) => (
								<article className="card" key={story.id}>
									<div className="inline">
										<Badge tone={toneForStatus(story.status)}>{story.status}</Badge>
										<strong className="card-title">{story.title}</strong>
									</div>
									{story.asA || story.iWant || story.soThat ? (
										<p className="card-body">
											{t('app.workbench.loop.story.asA', 'As a')} {story.asA};{' '}
											{t('app.workbench.loop.story.iWant', 'I want')} {story.iWant};{' '}
											{t('app.workbench.loop.story.soThat', 'so that')} {story.soThat}
										</p>
									) : null}
								</article>
							))}
						</div>
					</Labeled>
				) : null}
				{tasks.length ? (
					<Labeled label={t('app.workbench.loop.backlog.tasks', 'Agent tasks')}>
						<div className="stack compact">
							{tasks.map((task) => (
								<div className="inline" key={task.id}>
									<Badge tone={toneForStatus(task.status)}>{task.status}</Badge>
									<span className="mono">{task.role}</span>
									<span>{task.title}</span>
								</div>
							))}
						</div>
					</Labeled>
				) : null}
			</div>
		);
	}

	const iterations = data?.iterations ?? [];
	if (!iterations.length) {
		return (
			<EmptyState
				title={t('app.workbench.loop.iteration.emptyTitle', 'No planned iterations')}
				body={t(
					'app.workbench.loop.iteration.emptyBody',
					'Planned iterations appear here once the iteration planner runs against the approved backlog.',
				)}
			/>
		);
	}
	return (
		<div className="stack">
			{iterations.map((iteration) => (
				<article className="card" key={iteration.id}>
					<div className="inline">
						<Badge tone={toneForStatus(iteration.status)}>{iteration.status}</Badge>
						<strong className="card-title">{iteration.title}</strong>
						<span className="mono">{iteration.workspaceStrategy}</span>
						<span className="muted">
							{iteration.taskCount} {t('app.workbench.loop.iteration.tasksLabel', 'planned tasks')}
						</span>
					</div>
					{iteration.goal ? <p className="card-body">{iteration.goal}</p> : null}
				</article>
			))}
		</div>
	);
}
