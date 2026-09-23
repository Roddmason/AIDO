/**
 * Development-stage signal of a thread, derived from the same event log and pipeline derivation the
 * execution panel renders, so the conversation switches into the story board layout exactly when the
 * DeveloperAgent starts executing stories (and back when a new objective restarts planning).
 * @author Rodrigo Mason
 */
import { useMemo } from 'react';

import type { ThreadAgentEvent } from '../../api/types';
import { derivePipeline, EXECUTING_STEP_INDEX } from './ThreadExecutionPanel';

export type ThreadStage = {
	/** Index of the pipeline step the newest milestone made current. */
	current: number;
	blocked: boolean;
	/** True once the newest milestone reached executing (including QA, security, approval, delivery). */
	inDevelopment: boolean;
};

/** Pipeline position of the thread for the layout switch. */
export function useThreadStage(events: ThreadAgentEvent[], threadStatus: string): ThreadStage {
	return useMemo(() => {
		const pipeline = derivePipeline(events, threadStatus, '');
		return {
			current: pipeline.current,
			blocked: pipeline.blocked,
			inDevelopment: pipeline.current >= EXECUTING_STEP_INDEX,
		};
	}, [events, threadStatus]);
}
