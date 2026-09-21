/**
 * Shares thread invalidation between the conversation and inspector without remounting either.
 * @author Rodrigo Mason
 */
import { createContext, useContext } from 'react';

export const ThreadRefreshContext = createContext<{
	threadId: string | null;
	revision: number;
	invalidate: (threadId: string) => void;
} | null>(null);

/** Returns the shell-owned refresh signal for the active thread. */
export function useThreadRefresh() {
	const context = useContext(ThreadRefreshContext);
	if (!context) throw new Error('Thread refresh requires the shell context.');
	return context;
}
