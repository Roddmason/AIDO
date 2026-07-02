# Threads Chat Transition (Depth Dissolve) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Animate the handoff from the "new thread" intake screen to the live thread view with a
3D "depth dissolve" (using the `motion` library already installed), and make sure the WHOLE chat
block (title + transcript + composer) — not just its header — stays pinned at the top while the
execution console scrolls below it.

**Architecture:** Pure frontend/visual change inside `local-control-center/web`. No backend, data
model, coordinator, or API changes (explicit user instruction: frontend + UX/UI only, no logic).
`ThreadConversation.tsx` is refactored from early-`return`-per-branch to a single `content`
variable computed by an if/else-if chain (preserving today's TypeScript narrowing), wrapped once
in `AnimatePresence mode="popLayout"` keyed by a derived `phase`. Two new declarative variants in
`motion/variants.ts` drive the 3D entrance/exit; everything else (data hooks, API calls, message
rendering) is untouched.

**Tech Stack:** React 18, TypeScript, `motion` 12.40.0 (`LazyMotion domMax` + `MotionConfig
reducedMotion="user"`, already wired in `main.tsx`), Vite, Playwright (`tests_web`), Biome.

**Design reference:** `docs/superpowers/specs/2026-06-30-threads-chat-transition-design.md`.

**IMPORTANT — re-verify before editing:** this repo has a second, independent autonomous agent
that also commits and pushes to `dev` (confirmed present in this session: 7 commits landed on
`dev` between this plan's base commit and the start of implementation, including a change to the
exact CSS this plan touches — see Task 1). Before touching any file this plan names, re-read its
CURRENT content on disk / `git show HEAD:<path>` rather than trusting a diff shown verbatim below
— treat the code blocks in this plan as "last verified against commit `7ae03ca`," not as guaranteed
current truth. If what you find on disk differs from what a step shows, that is expected (not a
sign you have the wrong file) — adapt the edit to the real surrounding code and note the drift in
your report.

## Global Constraints

- No new npm/pnpm dependencies — `motion` already covers 3D transforms, springs, and
  `AnimatePresence`.
- No changes to `local_control_center/` (Python backend), API contracts, or thread data flow.
- All new motion variants must follow the existing reduced-motion pattern: only `transform` +
  `opacity` properties change between states, so `MotionConfig reducedMotion="user"` (already
  configured in `main.tsx`) strips the transforms automatically and leaves a plain fade.
- No new colors/gradients/shadows — visual guardrails already enforced by `pnpm run test:py`
  (no Inter/cyan/purple/gradients, no side-stripe borders).
- Every task ends green on: `pnpm run check:web`, `pnpm run typecheck:web`.
- Commits follow `Tipo (Ámbito): mensaje detallado` (this repo's convention) and only touch the
  files listed in that task.
- Working directly on `dev` (no worktree/feature branch): this repo's GitHub ruleset blocks
  create/push/delete on any branch except `dev`, and the established workflow here is to commit
  and push straight to `dev` per finished piece of work.

---

### Task 1: Make the WHOLE chat block sticky (not just its header)

**Owner:** `frontend-developer-senior` (implement) · `code-reviewer-senior` (review).

**Current state (verified at commit `7ae03ca`, re-verify before editing):** the other agent
working in this repo already added `position: sticky` — but only to `.thread-chat-sticky
.thread-conversation-head` (the small title + status-chip row), not to `.thread-chat-sticky`
itself (the block that also contains the transcript and the composer). It also already added a
Playwright test for this, `tests_web/threads.spec.js:102-162` ("Threads: the chat header stays
pinned while the execution console scrolls"), which scrolls `.content-frame` (the real scroll
ancestor — `.thread-live-layout` itself carries no `overflow` today) and asserts
`.thread-conversation-head`'s bounding box is unchanged. That satisfies "the title never
disappears" but not the actual request: "el chat quede arriba siempre visible" — the whole chat
(messages + composer), not only the heading. This task fixes that gap.

**Files:**
- Modify: `local-control-center/web/src/design-system/layout.css` (the `.thread-chat-sticky` /
  `.thread-chat-sticky .thread-conversation-head` rules, currently around line 1373-1388)
- Modify: `tests_web/threads.spec.js` (extend the existing test at line 102, do not duplicate it)

**Interfaces:**
- Consumes: existing classes `.thread-chat-sticky`, `.thread-conversation-head`,
  `.thread-execution-console`, `.content-frame` (no renames, no JS/TSX changes in this task).
- Produces: a strengthened Playwright regression test proving the whole chat block — not just its
  header — stays pinned; Task 2 and Task 3 rely on this staying green.

- [ ] **Step 1: Read the current CSS and confirm the exact text to change**

Read `local-control-center/web/src/design-system/layout.css` and find the three rules
`.thread-live-layout`, `.thread-chat-sticky`, and `.thread-chat-sticky .thread-conversation-head`.
As last verified, they read:

```css
.thread-live-layout {
	gap: var(--space-4);
	padding: var(--space-4) var(--space-6) var(--space-6);
}

.thread-chat-sticky {
	display: grid;
	gap: var(--space-3);
	padding: 0 0 var(--space-3);
	background: var(--color-surface-workbench);
	border-bottom: 1px solid var(--color-border-subtle);
}

.thread-chat-sticky .thread-conversation-head {
	position: sticky;
	top: 0;
	z-index: 4;
	padding: var(--space-2) 0;
	border-bottom: none;
	background: var(--color-surface-workbench);
}
```

If this doesn't match what you find, work from what's actually on disk — the fix below is a
description of the change, not a literal patch to force through.

- [ ] **Step 2: Move `position: sticky` from the header to the whole chat block**

Change it to:

```css
.thread-live-layout {
	gap: var(--space-4);
	padding: var(--space-4) var(--space-6) var(--space-6);
}

.thread-chat-sticky {
	position: sticky;
	top: 0;
	z-index: 4;
	display: grid;
	gap: var(--space-3);
	padding: 0 0 var(--space-3);
	background: var(--color-surface-workbench);
	border-bottom: 1px solid var(--color-border-subtle);
}

.thread-chat-sticky .thread-conversation-head {
	padding: var(--space-2) 0;
	border-bottom: none;
	background: transparent;
}
```

`.thread-live-layout` itself is unchanged — `.content-frame` (an ancestor, shared with other
routes, `overflow: auto` already set on it in `layout.css`) is the actual scrolling ancestor that
`position: sticky` resolves against, exactly as the existing test already assumes. Do not add
`overflow-y: auto` to `.thread-live-layout` — that would create a second, redundant scroll
container and could break the existing test's `.content-frame` overflow measurement.

- [ ] **Step 3: Strengthen the existing Playwright test**

Open `tests_web/threads.spec.js`. Find the test `'Threads: the chat header stays pinned while the
execution console scrolls'` (around line 102). Rename it and add a second locator/assertion pair
for `.thread-chat-sticky` alongside the existing `.thread-conversation-head` one, reusing the same
before/after scroll positions (do not re-scroll or duplicate the setup). The test becomes:

```js
test('Threads: the whole chat block stays pinned while the execution console scrolls', async ({
	page,
}) => {
	await page.setViewportSize({ width: 1280, height: 560 });
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	const firstMessage = `Add a pinned thread layout endpoint ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.getByText(/Run queued|Run encolado/).first()).toBeVisible({ timeout: 20_000 });

	const project = await getActiveProject(page);
	const listed = await page.request.get(`/api/v1/threads?projectId=${project.id}`);
	const { threads } = await listed.json();
	const created = threads.find((thread) => thread.title === firstMessage.slice(0, 80));
	expect(created).toBeTruthy();
	const token = await getWriteToken(page);
	for (let index = 0; index < 6; index += 1) {
		const response = await page.request.post(`/api/v1/threads/${created.id}/messages`, {
			headers: { 'X-Local-Control-Token': token },
			data: { content: `Add pinned layout overflow event ${index} ${Date.now()}` },
		});
		expect(response.ok()).toBe(true);
	}

	const scrollRegion = page.locator('.content-frame').first();
	const stickyHeader = page.locator('.thread-conversation-head').first();
	const stickyChat = page.locator('.thread-chat-sticky').first();

	// Precondition: the thread surface must actually overflow the viewport, otherwise the assertions
	// below would pass vacuously.
	await expect
		.poll(() => scrollRegion.evaluate((node) => node.scrollHeight - node.clientHeight), {
			timeout: 20_000,
		})
		.toBeGreaterThan(260);

	await scrollRegion.evaluate((node) => {
		node.scrollTop = 120;
	});
	await page.waitForTimeout(150);
	const headerBefore = await stickyHeader.boundingBox();
	const chatBefore = await stickyChat.boundingBox();
	expect(headerBefore).not.toBeNull();
	expect(chatBefore).not.toBeNull();

	await scrollRegion.evaluate((node) => {
		node.scrollTop = 240;
	});
	await page.waitForTimeout(150);

	const scrollTop = await scrollRegion.evaluate((node) => node.scrollTop);
	expect(scrollTop).toBeGreaterThan(0);

	const headerAfter = await stickyHeader.boundingBox();
	const chatAfter = await stickyChat.boundingBox();
	expect(headerAfter).not.toBeNull();
	expect(chatAfter).not.toBeNull();
	expect(Math.round(headerAfter.y)).toBe(Math.round(headerBefore.y));
	// The whole chat block (title + transcript + composer) stays pinned, not just the title — before
	// this task only the header had `position: sticky`.
	expect(Math.round(chatAfter.y)).toBe(Math.round(chatBefore.y));
});
```

- [ ] **Step 4: Run the test against the fixed code**

Run: `node scripts/run-web-tests.mjs tests_web/threads.spec.js -g "stays pinned"`
Expected: PASS. If it fails, read the actual failure — do not guess; report BLOCKED with the
output if you cannot resolve it after checking the CSS specificity and the scroll target.

- [ ] **Step 5: Run the full web test file to confirm no regressions**

Run: `node scripts/run-web-tests.mjs tests_web/threads.spec.js`
Expected: every test in the file PASSES.

- [ ] **Step 6: Commit**

```bash
git add tests_web/threads.spec.js local-control-center/web/src/design-system/layout.css
git commit -m "Fix (Threads): fija el bloque de chat completo (no solo el titulo) al hacer scroll en la consola de ejecucion"
```

---

### Task 2: Depth-dissolve transition (variants + `AnimatePresence` orchestration)

**Owner:** `frontend-developer-senior` (implement) · `accessibility-qa` (review timing,
reduced-motion fallback, no new AI-slop visual elements).

**Files:**
- Modify: `local-control-center/web/src/motion/variants.ts`
- Modify: `local-control-center/web/src/design-system/layout.css` (new rules only, appended near
  the end of the `/* ---- Real thread conversation (threads route center) ---- */` section)
- Modify: `local-control-center/web/src/features/shell/ThreadConversation.tsx`
- Test: `tests_web/threads.spec.js` (append)

**Interfaces:**
- Consumes: `EASE_OUT`, `panelTransition` (already exported from `motion/variants.ts`); `m`,
  `AnimatePresence` from `motion/react`.
- Produces: two new named exports from `motion/variants.ts` — `threadIntakeExit: Variants` and
  `threadLiveEnter: Variants`.

**Before you start:** re-read `local-control-center/web/src/features/shell/ThreadConversation.tsx`
in full — Task 1 (a different implementer) may have just changed the CSS this file relies on, and
the component itself may have been touched by the other autonomous agent working in this repo
since this plan was written. As last verified (commit `7ae03ca`), the component already has a
`useCallback`-wrapped `sendMessage`/`resolveDecision` pair (they bump a `streamRefreshKey` after
each write) and a `mergeConsoleEvents(detail.events, eventStream.events)` helper for the console
list — both must be preserved exactly; this task only adds the phase/AnimatePresence layer around
the existing four early-return branches plus the live-layout return.

- [ ] **Step 1: Write the characterization tests first**

Append to `tests_web/threads.spec.js` (two tests: a reduced-motion completion check, and a
regression check that thread creation still renders the full live layout after the refactor):

```js
test('Threads: creating a thread still renders the live layout under reduced motion', async ({
	browser,
}) => {
	const context = await browser.newContext({ reducedMotion: 'reduce' });
	const page = await context.newPage();
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced');

	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	const firstMessage = `Reduced motion check ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();

	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.locator('.thread-chat-sticky').first()).toBeVisible();
	await expect(page.locator('.thread-execution-console').first()).toBeVisible();

	await context.close();
});

test('Threads: the new-thread to live-thread handoff renders the full layout', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	const firstMessage = `Handoff check ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();

	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.locator('.thread-chat-sticky').first()).toBeVisible();
	await expect(page.locator('.thread-execution-console').first()).toBeVisible();
	// The intake heading is gone once the live layout has taken over.
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeHidden();
});
```

- [ ] **Step 2: Run the new tests against the current (pre-refactor) code**

Run: `node scripts/run-web-tests.mjs tests_web/threads.spec.js -g "reduced motion|handoff"`
Expected: PASS (today's hard-cut implementation already satisfies these end-state assertions —
this confirms the safety net is valid before refactoring).

- [ ] **Step 3: Add the two new variants**

Modify `local-control-center/web/src/motion/variants.ts` — append after the existing `crossfade`
export, before `skeletonShimmer`:

```ts
/** Salida del intake al crear el hilo: se hunde con una leve rotación 3D antes de desvanecerse. */
export const threadIntakeExit: Variants = {
	initial: { opacity: 0, y: 6 },
	animate: { opacity: 1, y: 0, transition: { duration: 0.2, ease: EASE_OUT } },
	exit: {
		opacity: 0,
		scale: 0.94,
		y: -14,
		rotateX: 8,
		transition: { duration: 0.4, ease: EASE_OUT },
	},
};

/** Entrada del hilo activo justo después de crearlo: se asienta desde una leve inclinación 3D. */
export const threadLiveEnter: Variants = {
	initial: { opacity: 0, scale: 0.98, y: 14, rotateX: -6 },
	animate: {
		opacity: 1,
		scale: 1,
		y: 0,
		rotateX: 0,
		transition: { type: 'spring', stiffness: 170, damping: 22, delay: 0.12 },
	},
	exit: { opacity: 0, transition: { duration: 0.12 } },
};
```

- [ ] **Step 4: Add the perspective stage CSS**

Modify `local-control-center/web/src/design-system/layout.css` — append right after the
`.thread-console-chip` rule (immediately before the `@media (max-width: 720px)` block for
threads):

```css
.thread-conversation-stage {
	height: 100%;
	min-height: 0;
	display: flex;
	flex-direction: column;
	perspective: 1200px;
}

.thread-conversation-phase {
	height: 100%;
	min-height: 0;
	display: flex;
	flex-direction: column;
}
```

- [ ] **Step 5: Update the imports in `ThreadConversation.tsx`**

Add to the existing imports (re-read the file first and adapt — as last verified, the current
imports are):

```tsx
import { m } from 'motion/react';
import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useState } from 'react';
import { createThread, postThreadMessage } from '../../api/client';
import type { Overview, Project, ThreadAgentEvent, ThreadArtifact, ThreadMessage } from '../../api/types';
import type { Mutate } from '../../app/routes';
import { Button, EmptyState, Skeleton, StatusChip, TextArea } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { cardTransition, listStagger, panelTransition } from '../../motion/variants';
```

becomes:

```tsx
import { AnimatePresence, m } from 'motion/react';
import {
	type FormEvent,
	type KeyboardEvent,
	type ReactNode,
	useCallback,
	useEffect,
	useRef,
	useState,
} from 'react';
import { createThread, postThreadMessage } from '../../api/client';
import type {
	Overview,
	Project,
	ThreadAgentEvent,
	ThreadArtifact,
	ThreadDetail,
	ThreadMessage,
} from '../../api/types';
import type { Mutate } from '../../app/routes';
import { Button, EmptyState, Skeleton, StatusChip, TextArea } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import {
	cardTransition,
	EASE_OUT,
	listStagger,
	panelTransition,
	threadIntakeExit,
	threadLiveEnter,
} from '../../motion/variants';
```

(keep every other import line — `lucide-react`, `GitBranchBar`, `useSettings`, `NEW_SESSION_ID`,
`useThreadConversation`, `useThreadEventStream` — exactly as they are.)

- [ ] **Step 6: Add the two phase helpers, right before `export function ThreadConversation`**

```tsx
type ConversationPhase = 'no-project' | 'new' | 'loading' | 'error' | 'live';

/** Derives the AnimatePresence phase id from the same conditions the render branches check below. */
function deriveConversationPhase(
	selectedProject: Project | null,
	isNew: boolean,
	loading: boolean,
	error: boolean,
	detail: ThreadDetail | null,
): ConversationPhase {
	if (!selectedProject) return 'no-project';
	if (isNew) return 'new';
	if (loading && !detail) return 'loading';
	if (error || !detail) return 'error';
	return 'live';
}

/** Picks the 3D depth-dissolve variant only for the intake exit and the live entrance that follows
 *  it; every other phase change (loading, error, switching between already-created threads) gets
 *  the existing plain `panelTransition` fade. */
function variantsForPhase(phase: ConversationPhase, handoffFromIntake: boolean) {
	if (phase === 'new') return threadIntakeExit;
	if (phase === 'live' && handoffFromIntake) return threadLiveEnter;
	return panelTransition;
}
```

(`ConversationPhase`/`deriveConversationPhase`/`variantsForPhase` avoid a nested ternary, which
Biome's `complexity/noNestedTernary` — part of this repo's `recommended` ruleset in `biome.json` —
would flag on `check:web`.)

- [ ] **Step 7: Refactor the component body**

Replace the whole `ThreadConversation` function (from `export function ThreadConversation({`
through its closing `}`, right before the `/** Research report card... */` comment) with:

```tsx
export function ThreadConversation({
	overview,
	selectedProject,
	selectedThreadId,
	mutate,
	token,
	onSelectThread,
	onCreateProject,
}: ThreadConversationProps) {
	const { t } = useI18n();
	const isNew = !selectedThreadId || selectedThreadId === NEW_SESSION_ID;
	const activeThreadId = isNew ? null : selectedThreadId;
	const { detail, loading, error, busy, reload, send, resolve } = useThreadConversation(
		activeThreadId,
		mutate,
	);
	const [streamRefreshKey, setStreamRefreshKey] = useState(0);
	const eventStream = useThreadEventStream(activeThreadId, streamRefreshKey);

	// Git mutations inside the composer re-pull the overview (fire-and-forget) so the shell stays in sync.
	const refreshOverview = () => {
		void mutate(async () => undefined, { awaitRefresh: false });
	};

	useEffect(() => {
		if (!activeThreadId || eventStream.events.length === 0) return;
		reload();
	}, [activeThreadId, eventStream.events.length, eventStream.threadStatus, reload]);

	const sendMessage = useCallback(
		async (content: string) => {
			await send(content);
			setStreamRefreshKey((value) => value + 1);
		},
		[send],
	);

	const resolveDecision = useCallback(
		async (decisionId: string, resolution: string) => {
			await resolve(decisionId, resolution);
			setStreamRefreshKey((value) => value + 1);
		},
		[resolve],
	);

	// One phase id per render, used only to pick the AnimatePresence key/variant below; the
	// if/else-if chain further down re-checks the same conditions directly so TypeScript's
	// narrowing of `selectedProject`/`detail` keeps working exactly as before this refactor.
	const phase = deriveConversationPhase(selectedProject, isNew, loading, error, detail);

	// Remembers the previous phase so the live layout only plays the 3D "depth dissolve" entrance
	// right after leaving the new-thread intake. Switching between two already-created threads never
	// changes `phase` (both are 'live'), and recovering from loading/error uses a plain fade.
	const previousPhaseRef = useRef(phase);
	const handoffFromIntake = previousPhaseRef.current === 'new' && phase === 'live';
	useEffect(() => {
		previousPhaseRef.current = phase;
	}, [phase]);

	let content: ReactNode;

	if (!selectedProject) {
		content = (
			<div className="shell-chat-layout">
				<div className="shell-chat-transcript-empty">
					<EmptyState
						title={t('app.threads.emptyTitle', 'No thread selected')}
						body={t('app.threads.emptyBody', 'Pick a thread on the left or start a new one.')}
						action={
							<Button variant="primary" onClick={onCreateProject}>
								{t('app.shell.threads.openFolder', 'Open folder')}
							</Button>
						}
					/>
				</div>
			</div>
		);
	} else if (isNew) {
		content = (
			<NewThreadComposer
				overview={overview}
				project={selectedProject}
				mutate={mutate}
				token={token}
				onGitRefresh={refreshOverview}
				onCreated={onSelectThread}
			/>
		);
	} else if (loading && !detail) {
		content = (
			<div className="shell-chat-layout">
				<div className="shell-chat-transcript">
					<Skeleton className="thread-skeleton-row" />
					<Skeleton className="thread-skeleton-row" />
					<Skeleton className="thread-skeleton-row" />
				</div>
			</div>
		);
	} else if (error || !detail) {
		content = (
			<div className="shell-chat-layout">
				<div className="shell-chat-transcript-empty">
					<EmptyState
						title={t('app.threads.loadErrorTitle', 'Could not load this thread')}
						body=""
						action={
							<Button variant="secondary" onClick={reload}>
								{t('app.threads.retry', 'Retry')}
							</Button>
						}
					/>
				</div>
			</div>
		);
	} else {
		const pendingDecision = detail.decisions.find((decision) => decision.status === 'pending');
		const researchArtifacts = detail.artifacts.filter((artifact) => artifact.kind === 'research_report');
		const consoleEvents = mergeConsoleEvents(detail.events, eventStream.events);
		const threadStatus = eventStream.threadStatus ?? detail.thread.status;
		const waitingForWorker =
			threadStatus === 'queued' && !consoleEvents.some((event) => event.type === 'worker_claimed');

		content = (
			<div className="shell-chat-layout thread-live-layout">
				<section className="thread-chat-sticky" aria-label={t('app.threads.chatRegion', 'Thread chat')}>
					<header className="thread-conversation-head">
						<div className="thread-conversation-title">
							<MessageSquare aria-hidden="true" size={16} />
							<h2>{detail.thread.title}</h2>
						</div>
						<StatusChip tone={toneForStatus(threadStatus)}>{threadStatus.replace(/_/g, ' ')}</StatusChip>
					</header>

					<div className="thread-chat-transcript" aria-live="polite">
						{detail.messages.length === 0 ? (
							<p className="thread-chat-empty">
								{t('app.threads.transcriptEmpty', 'No messages yet. Send the first one below.')}
							</p>
						) : (
							detail.messages.map((message) => <ThreadMessageRow key={message.id} message={message} />)
						)}
					</div>

					{pendingDecision ? (
						<m.section
							className="thread-decision-console"
							aria-label={t('app.threads.decisionTitle', 'Decision needed')}
							variants={panelTransition}
							initial="initial"
							animate="animate"
						>
							<div className="thread-decision-head">
								<AlertTriangle aria-hidden="true" size={15} />
								<strong>{t('app.threads.decisionTitle', 'Decision needed')}</strong>
							</div>
							<p>{pendingDecision.prompt}</p>
							<div className="thread-decision-options">
								{pendingDecision.options.map((option) => (
									<Button
										key={option}
										variant="secondary"
										disabled={busy}
										onClick={() => resolveDecision(pendingDecision.id, option)}
									>
										{option}
									</Button>
								))}
							</div>
						</m.section>
					) : null}

					<ThreadComposerBox
						project={selectedProject}
						token={token}
						onGitRefresh={refreshOverview}
						value=""
						busy={busy}
						rows={3}
						submitLabel={t('app.threads.send', 'Send')}
						submitBusyLabel={t('app.threads.sending', 'Sending…')}
						onSendMessage={sendMessage}
					/>
				</section>

				<m.section
					className="thread-execution-console"
					role="log"
					aria-live="polite"
					aria-relevant="additions"
					aria-label={t('app.threads.consoleRegion', 'Execution console')}
					initial={{ opacity: 0, y: 6 }}
					animate={{
						opacity: 1,
						y: 0,
						transition: { duration: 0.2, ease: EASE_OUT, delay: handoffFromIntake ? 0.55 : 0 },
					}}
				>
					<div className="thread-console-title">
						<span>{t('app.threads.consoleTitle', 'Execution')}</span>
						{eventStream.loading ? (
							<StatusChip tone="pending">{t('app.threads.consoleLoading', 'syncing')}</StatusChip>
						) : null}
					</div>
					{eventStream.error ? (
						<div className="thread-console-status" data-tone="danger">
							{eventStream.error}
						</div>
					) : null}
					{waitingForWorker ? (
						<div className="thread-console-status" data-tone="pending">
							{t('app.threads.waitingWorker', 'Queued: waiting for a worker to claim this run.')}
						</div>
					) : null}
					{consoleEvents.length ? (
						consoleEvents.map((event) => <ThreadConsoleRow key={event.id} event={event} />)
					) : (
						<div className="thread-console-status" data-tone="muted">
							{t('app.threads.consoleEmpty', 'No execution events yet.')}
						</div>
					)}
					{researchArtifacts.map((artifact) => (
						<ThreadResearchCard key={artifact.id} artifact={artifact} />
					))}
				</m.section>
			</div>
		);
	}

	const stageVariants = variantsForPhase(phase, handoffFromIntake);

	return (
		<div className="thread-conversation-stage">
			<AnimatePresence mode="popLayout" initial={false}>
				<m.div
					className="thread-conversation-phase"
					key={phase}
					variants={stageVariants}
					initial="initial"
					animate="animate"
					exit="exit"
				>
					{content}
				</m.div>
			</AnimatePresence>
		</div>
	);
}
```

Do not touch `mergeConsoleEvents`, `ThreadResearchCard`, `ThreadMessageRow`, `ThreadConsoleRow`,
`ThreadComposerBox`, `NewThreadComposer`, or any other function below this one — they are unrelated
to this task and already correct.

- [ ] **Step 8: Run the typechecker and Biome**

Run: `pnpm run typecheck:web`
Expected: no errors (`cardTransition`/`listStagger` stay imported — they're still used by
`ThreadMessageRow`/`NewThreadComposer` further down in the same file).

Run: `pnpm run check:web`
Expected: exit 0.

- [ ] **Step 9: Run the tests from Step 1 again, plus the full file**

Run: `node scripts/run-web-tests.mjs tests_web/threads.spec.js`
Expected: every test in the file PASSES, including Task 1's pinned-chat test and both tests added
in Step 1 of this task.

- [ ] **Step 10: Commit**

```bash
git add local-control-center/web/src/motion/variants.ts \
        local-control-center/web/src/design-system/layout.css \
        local-control-center/web/src/features/shell/ThreadConversation.tsx \
        tests_web/threads.spec.js
git commit -m "Feature (Threads): transicion Depth dissolve entre el intake de hilo nuevo y el hilo activo"
```

---

### Task 3: Visual verification and full gate run

**Owner:** `e2e-visual-qa` (capture + gates). No push from inside this task — the controller
handles the final whole-branch review and push separately after this task reports back.

**Files:** none (verification only; this task produces no diff beyond what Tasks 1-2 already
committed).

**Interfaces:** none — this task only runs commands and inspects output.

- [ ] **Step 1: Start the real app and capture the handoff visually**

Start the control center (`pnpm run start`, or reuse an already-running instance on port 4310 if
one exists — check before starting a second one) and, using available browser tooling, navigate
to `#threads`, create a new thread, and capture screenshots immediately before and ~0.6s after
submitting the first message. Save them under the scratchpad directory (not the repo).

Confirm visually:
- The intake card visibly recedes/rotates rather than just disappearing.
- The WHOLE title+transcript+composer block is in its final sticky position and not clipped or
  jittering once settled — not just the title.
- The execution console visibly appears after the chat block has settled, not simultaneously.
- No new gradients, glow, or off-palette colors were introduced.

If any of these don't hold, that's expected tuning territory (the `0.55` delay, the
`rotateX`/`scale` magnitudes, the spring `stiffness`/`damping`) — adjust the values in
`motion/variants.ts` / `ThreadConversation.tsx` and re-verify. Report what you changed and why.

- [ ] **Step 2: Confirm reduced-motion still looks correct**

Re-run the same flow with the OS/browser "reduce motion" preference enabled and confirm by
screenshot that the handoff is a plain, instant-feeling crossfade with no jitter. (The Playwright
test from Task 2 Step 1 already covers this functionally — this step is the visual confirmation on
top of that.)

- [ ] **Step 3: Run the full web gate**

Run, in order, and read the real exit code of each:

```bash
pnpm run check:web
pnpm run typecheck:web
pnpm run build:web
pnpm run test:web
```

Expected: all four exit 0.

- [ ] **Step 4: Run the Python i18n/architecture gates**

Run: `pnpm run test:py`
Expected: exit 0.

- [ ] **Step 5: Report back**

Report DONE with: a summary of the visual check (Step 1-2), the four gate results (Step 3), the
`test:py` result (Step 4), and any tuning changes made. Do not push — the controller does the
final whole-branch review and push after this report.
