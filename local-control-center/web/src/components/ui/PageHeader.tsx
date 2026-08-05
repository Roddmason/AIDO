/**
 * Encabezado estándar de página (kicker + título h1 + resumen) que participa en las
 * animaciones de entrada vía `data-motion-item`; da jerarquía consistente entre rutas.
 * @author Rodrigo Mason
 */
export function PageHeader({
	kicker,
	title,
	summary,
}: {
	kicker: string;
	title: string;
	summary: string;
}) {
	return (
		<header className="page-header" data-motion-item>
			<div className="page-kicker">{kicker}</div>
			{/* h2: el único h1 de la app es la marca del MenuBar; el estilo viene de la clase. */}
			<h2 className="page-title">{title}</h2>
			<p className="page-summary">{summary}</p>
		</header>
	);
}
