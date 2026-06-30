# Threads Chat Transition (Depth Dissolve) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Animate the handoff from the "new thread" intake screen to the live thread view with a
3D "depth dissolve" (using the `motion` library already installed), and verify/fix the sticky
chat layout so the title+chat block stays pinned at the top while the execution console scrolls
below it.

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

---

### Task 1: Verify and (if needed) fix the sticky chat layout

**Owner:** `frontend-developer-senior` (implement) · `frontend-architect` (review the CSS/layout
chain doesn't regress other routes sharing `.content-frame`).

**Files:**
- Modify (conditionally, only if Step 2 fails): `local-control-center/web/src/design-system/layout.css:1368-1372`
- Test: `tests_web/threads.spec.js` (append)

**Interfaces:**
- Consumes: existing classes `.thread-chat-sticky`, `.thread-live-layout`, `.thread-execution-console`
  (no renames, no JS changes in this task).
- Produces: a permanent Playwright regression test proving the sticky-chat-while-console-scrolls
  contract, reused as a safety net by Task 2 and Task 3.

- [ ] **Step 1: Write the characterization test**

Append to `tests_web/threads.spec.js`:

```js
test('Threads: the chat header stays pinned while the execution console scrolls', async ({
	page,
}) => {
	await page.setViewportSize({ width: 1280, height: 560 });
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	const firstMessage = `Pin check ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.getByText(/Run queued|Run encolado/).first()).toBeVisible({ timeout: 20_000 });

	const scrollRegion = page.locator('.thread-live-layout').first();
	const sticky = page.locator('.thread-chat-sticky').first();

	// Precondition: the console must actually overflow the viewport, otherwise the assertions
	// below would pass vacuously. If this fails, shrink the viewport height further above.
	const overflowAmount = await scrollRegion.evaluate(
		(node) => node.scrollHeight - node.clientHeight,
	);
	expect(overflowAmount).toBeGreaterThan(0);

	const before = await sticky.boundingBox();
	expect(before).not.toBeNull();

	await scrollRegion.evaluate((node) => {
		node.scrollTop = node.scrollHeight;
	});
	await page.waitForTimeout(150);

	const scrollTop = await scrollRegion.evaluate((node) => node.scrollTop);
	expect(scrollTop).toBeGreaterThan(0);

	const after = await sticky.boundingBox();
	expect(after).not.toBeNull();
	expect(Math.round(after.y)).toBe(Math.round(before.y));
});
```

- [ ] **Step 2: Run the test against the current code**

Run: `node scripts/run-web-tests.mjs tests_web/threads.spec.js -g "stays pinned"`

Two possible outcomes:
- **PASS** — the existing `position: sticky` + `overflow-y: auto` chain already works. Skip
  Step 3, go straight to Step 4.
- **FAIL** (`overflowAmount` not > 0, or `after.y !== before.y`) — continue to Step 3.

- [ ] **Step 3 (only if Step 2 failed): Make the sizing chain explicit**

Modify `local-control-center/web/src/design-system/layout.css` — current block at line 1368:

```css
.thread-live-layout {
	overflow-y: auto;
	gap: var(--space-4);
	padding: var(--space-4) var(--space-6) var(--space-6);
}
```

becomes:

```css
.thread-live-layout {
	flex: 1 1 auto;
	min-height: 0;
	overflow-y: auto;
	gap: var(--space-4);
	padding: var(--space-4) var(--space-6) var(--space-6);
}
```

Re-run Step 2's command and confirm PASS before continuing.

- [ ] **Step 4: Run the full web test file to confirm no regressions**

Run: `node scripts/run-web-tests.mjs tests_web/threads.spec.js`
Expected: all tests in the file PASS, including the new one.

- [ ] **Step 5: Commit**

```bash
git add tests_web/threads.spec.js local-control-center/web/src/design-system/layout.css
git commit -m "Test (Threads): verifica que el chat sticky se mantenga fijo mientras la consola de ejecucion hace scroll"
```

(Drop `layout.css` from `git add` if Step 2 passed and Step 3 was skipped.)

---

### Task 2: Depth-dissolve transition (variants + `AnimatePresence` orchestration)

**Owner:** `react-senior-dev` or `frontend-developer-senior` (implement) · `ux-ui-director` +
`accessibility-qa` (review timing, reduced-motion fallback, no new AI-slop visual elements).

**Files:**
- Modify: `local-control-center/web/src/motion/variants.ts`
- Modify: `local-control-center/web/src/design-system/layout.css` (new rules, appended near the
  `/* ---- Real thread conversation (threads route center) ---- */` block, after line 1532 or any
  current end of that section)
- Modify: `local-control-center/web/src/features/shell/ThreadConversation.tsx`
- Test: `tests_web/threads.spec.js` (append)

**Interfaces:**
- Consumes: `EASE_OUT`, `panelTransition` (already exported from `motion/variants.ts`); `m`,
  `AnimatePresence` from `motion/react`.
- Produces: two new named exports from `motion/variants.ts` — `threadIntakeExit: Variants` and
  `threadLiveEnter: Variants` — for reuse if another "showcase" transition needs the same language
  later.

- [ ] **Step 1: Write the failing/characterization tests first**

Append to `tests_web/threads.spec.js` (two tests: a reduced-motion completion check, and a regression
check that the existing creation flow still renders the full live layout after the refactor):

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
export (after line 66, before `skeletonShimmer`):

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
`.thread-console-chip` rule (the block ending around line 1491, right before the
`@media (max-width: 720px)` block for threads):

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

Modify `local-control-center/web/src/features/shell/ThreadConversation.tsx:14-37` — replace:

```tsx
import {
	AlertTriangle,
	BookOpen,
	CalendarClock,
	Hash,
	Laptop,
	MessageSquare,
	Send,
	ShieldCheck,
} from 'lucide-react';
import { m } from 'motion/react';
import { type FormEvent, type KeyboardEvent, useEffect, useState } from 'react';
import { createThread, postThreadMessage } from '../../api/client';
import type { Overview, Project, ThreadAgentEvent, ThreadArtifact, ThreadMessage } from '../../api/types';
import type { Mutate } from '../../app/routes';
import { Button, EmptyState, Skeleton, StatusChip, TextArea } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { cardTransition, listStagger, panelTransition } from '../../motion/variants';
import { useSettings } from '../settings/useSettings';
import { NEW_SESSION_ID } from '../workbench/useWorkbenchData';
import { GitBranchBar } from './GitBranchBar';
import { useThreadConversation } from './useThreadConversation';
import { useThreadEventStream } from './useThreadEventStream';
```

with:

```tsx
import {
	AlertTriangle,
	BookOpen,
	CalendarClock,
	Hash,
	Laptop,
	MessageSquare,
	Send,
	ShieldCheck,
} from 'lucide-react';
import { AnimatePresence, m } from 'motion/react';
import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from 'react';
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
import { useSettings } from '../settings/useSettings';
import { NEW_SESSION_ID } from '../workbench/useWorkbenchData';
import { GitBranchBar } from './GitBranchBar';
import { useThreadConversation } from './useThreadConversation';
import { useThreadEventStream } from './useThreadEventStream';
```

- [ ] **Step 6: Refactor the component body**

Modify `local-control-center/web/src/features/shell/ThreadConversation.tsx` — the whole
`ThreadConversation` function (originally lines 89-283: from `export function ThreadConversation({`
through the final closing `}` before `/** Research report card... */`). Replace it with the two
helper functions below (Biome's `complexity/noNestedTernary` rule, part of the `recommended` set
in this repo's `biome.json`, forbids a ternary-inside-a-ternary — these helpers express the same
branching as plain `if` statements instead) followed by the refactored component:

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
	const eventStream = useThreadEventStream(activeThreadId);

	// Git mutations inside the composer re-pull the overview (fire-and-forget) so the shell stays in sync.
	const refreshOverview = () => {
		void mutate(async () => undefined, { awaitRefresh: false });
	};

	useEffect(() => {
		if (!activeThreadId || eventStream.events.length === 0) return;
		reload();
	}, [activeThreadId, eventStream.events.length, eventStream.threadStatus, reload]);

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
		const consoleEvents = eventStream.events.length ? eventStream.events : detail.events;
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
										onClick={() => resolve(pendingDecision.id, option)}
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
						onSendMessage={send}
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

- [ ] **Step 7: Run the typechecker and Biome**

Run: `pnpm run typecheck:web`
Expected: no errors (no new `any`, no unused imports — `cardTransition`/`listStagger` are still
used elsewhere in this file by `ThreadMessageRow`/`NewThreadComposer`, so they stay imported).

Run: `pnpm run check:web`
Expected: exit 0 (Biome lint + format clean).

- [ ] **Step 8: Run the tests from Step 1 again, plus the full file**

Run: `node scripts/run-web-tests.mjs tests_web/threads.spec.js`
Expected: every test in the file PASSES, including Task 1's pinned-header test and both tests
added in Step 1 of this task.

- [ ] **Step 9: Commit**

```bash
git add local-control-center/web/src/motion/variants.ts \
        local-control-center/web/src/design-system/layout.css \
        local-control-center/web/src/features/shell/ThreadConversation.tsx \
        tests_web/threads.spec.js
git commit -m "Feature (Threads): transicion Depth dissolve entre el intake de hilo nuevo y el hilo activo"
```

---

### Task 3: Visual verification and full gate run

**Owner:** `e2e-visual-qa` (capture + visual review) · `technical-lead-senior` (final approval
before commit/push).

**Files:** none (verification only; this task produces no diff beyond what Tasks 1-2 already
committed).

**Interfaces:** none — this task only runs commands and inspects output.

- [ ] **Step 1: Start the real app and capture the handoff visually**

Start the control center (`pnpm run start` or the existing dev workflow for this repo) and, using
the available browser tooling (Playwright via `tests_web`, or chrome-devtools/`Claude_Preview`
MCP if attached to a running instance), navigate to `#threads`, create a new thread, and capture
screenshots immediately before and ~0.6s after submitting the first message. Save them under the
scratchpad directory (not the repo).

Confirm visually:
- The intake card visibly recedes/rotates rather than just disappearing.
- The title + chat block is in its final sticky position and not clipped or jittering once
  settled.
- The execution console visibly appears after the chat block has settled, not simultaneously.
- No new gradients, glow, or off-palette colors were introduced.

If any of these don't hold, return to Task 2 Step 6 and adjust the numeric values (the `0.55`
delay, the `rotateX`/`scale` magnitudes, or the spring `stiffness`/`damping`) — this is expected
tuning, not a sign the architecture is wrong.

- [ ] **Step 2: Confirm reduced-motion still looks correct**

Re-run the same flow with the OS/browser "reduce motion" preference enabled (or rely on the
Playwright test from Task 2 Step 1, which already covers this functionally) and confirm by
screenshot that the handoff is a plain, instant-feeling crossfade with no jitter.

- [ ] **Step 3: Run the full web gate**

Run, in order, and read the real exit code of each:

```bash
pnpm run check:web
pnpm run typecheck:web
pnpm run build:web
pnpm run test:web
```

Expected: all four exit 0.

- [ ] **Step 4: Run the Python i18n/architecture gates** (this change adds no new copy or Python
code, but these gates also assert the web build stays consistent with the catalog)

Run: `pnpm run test:py`
Expected: exit 0.

- [ ] **Step 5: Final review and push**

Have `technical-lead-senior` review the combined diff from Tasks 1-2 against the design spec
(`docs/superpowers/specs/2026-06-30-threads-chat-transition-design.md`) and this plan. If
approved, push the commits already made in Tasks 1-2:

```bash
git push origin dev
```
