/**
 * Envoltorio fino de `Surface` que da a cada panel del Model Gateway un encabezado titulado uniforme.
 * Existe para que los paneles no acoplen su layout directamente a la primitiva `Surface`.
 */
import type { ReactNode } from 'react';

import { Surface } from '../../components/primitives';

export function PanelShell({ title, children }: { title: string; children: ReactNode }) {
	return <Surface title={title}>{children}</Surface>;
}
