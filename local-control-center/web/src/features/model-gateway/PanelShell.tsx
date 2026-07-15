/**
 * Envoltorio fino de `Surface` que da a cada panel del Model Gateway un encabezado titulado uniforme.
 * Existe para que los paneles no acoplen su layout directamente a la primitiva `Surface`.
 * @author Rodrigo Mason
 */
import type { ReactNode } from 'react';

import { Surface } from '../../components/ui';

export function PanelShell({ title, children }: { title: string; children: ReactNode }) {
	return <Surface title={title}>{children}</Surface>;
}
