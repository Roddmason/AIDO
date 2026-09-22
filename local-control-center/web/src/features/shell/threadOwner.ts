/**
 * Resuelve en el cliente el workspace dueño de un hilo nuevo. Ese id viaja luego como
 * `workspaceId` de los runs del hilo (p. ej. el ResearchAgent), así que debe ser el workspace
 * raíz del proyecto y nunca uno efímero (prompt del ProductOwner) o el worktree de otro hilo.
 * @author Rodrigo Mason
 */
import type { Overview, Project } from '../../api/types';
import { samePath } from '../../lib/paths';

const UNUSABLE_WORKSPACE_STATUSES = new Set(['archived', 'deleted']);

/**
 * Owner id for a brand-new thread: the project's live root workspace (same path as the project),
 * else the project id. Other workspaces are skipped on purpose: the overview cannot reliably tell a
 * per-task copy from a durable one, and the backend remaps the project id to the root workspace.
 */
export function ownerIdForProject(overview: Overview, project: Project): string {
	const root = overview.runtimeWorkspaces.find(
		(workspace) =>
			workspace.projectId === project.id &&
			!workspace.archivedAt &&
			!UNUSABLE_WORKSPACE_STATUSES.has(workspace.status) &&
			samePath(workspace.path, project.path),
	);
	return root?.id ?? project.id;
}
