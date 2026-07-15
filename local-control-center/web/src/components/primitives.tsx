/**
 * Fachada de compatibilidad hacia la librería tipada `components/ui`: re-exporta las
 * primitivas (con los alias históricos `Modal`→Dialog y `Badge`→StatusChip) para que los
 * sitios existentes sigan funcionando contra una única implementación mientras migran a
 * importar desde `ui` directamente. No define componentes propios.
 * @author Rodrigo Mason
 */
export {
	DataTable,
	Dialog as Modal,
	Drawer,
	EmptyState,
	PageHeader,
	Skeleton,
	StatusChip as Badge,
	StatusDot,
	Surface,
} from './ui';
