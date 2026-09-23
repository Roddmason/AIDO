/**
 * Lets the thread conversation fold the shell inspector away when it switches into the story board
 * layout, so the board, the chat column and the explorer fit without the inspector auto-opening;
 * the inspector stays one click away.
 * @author Rodrigo Mason
 */
import { createContext, useContext } from 'react';

export type ShellInspectorControl = { collapseInspector: () => void };

export const ShellInspectorContext = createContext<ShellInspectorControl | null>(null);

/** Returns the shell-owned inspector control, or null outside the shell (callers then no-op). */
export function useShellInspector(): ShellInspectorControl | null {
	return useContext(ShellInspectorContext);
}
