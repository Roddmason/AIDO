/**
 * Sección de contenido tipo card (`.surface`, variante `flat`) con título opcional;
 * participa en las animaciones de entrada de página vía `data-motion-item`.
 * @author Rodrigo Mason
 */
import type { ReactNode } from 'react';

export function Surface({
	title,
	children,
	flat = false,
}: {
	title?: string;
	children: ReactNode;
	flat?: boolean;
}) {
	return (
		<section className={`surface${flat ? ' flat' : ''}`} data-motion-item>
			{title ? <h2 className="surface-title">{title}</h2> : null}
			{children}
		</section>
	);
}
