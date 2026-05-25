import type { ReactNode } from 'react';

import { Surface } from '../../components/primitives';

export function PanelShell({ title, children }: { title: string; children: ReactNode }) {
	return <Surface title={title}>{children}</Surface>;
}
