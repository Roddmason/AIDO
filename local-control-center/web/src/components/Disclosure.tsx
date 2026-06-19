/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';
import { useId, useState } from 'react';

/**
 * Accessible disclosure for a single collapsible section. Keeps dense tables and
 * advanced options behind progressive disclosure inside the Settings group cards.
 *
 * Each instance owns its open state (independent — several can be open at once).
 * The trigger lives inside an <h3> so heading order stays h1 (page) → h2 (group)
 * → h3 (section). The panel uses `hidden` when collapsed so it leaves the tab
 * order and the accessibility tree; the open transition is handled in CSS and is
 * neutralised under prefers-reduced-motion.
 */
export function Disclosure({
	title,
	children,
	summary,
	defaultOpen = false,
}: {
	title: string;
	children: ReactNode;
	summary?: ReactNode;
	defaultOpen?: boolean;
}) {
	const reactId = useId();
	const triggerId = `disclosure-trigger-${reactId}`;
	const regionId = `disclosure-region-${reactId}`;
	const [open, setOpen] = useState(defaultOpen);
	return (
		<div className="disclosure" data-open={open}>
			<h3 className="disclosure-heading">
				<button
					id={triggerId}
					className="disclosure-trigger"
					type="button"
					aria-expanded={open}
					aria-controls={regionId}
					onClick={() => setOpen((value) => !value)}
				>
					<ChevronDown className="disclosure-chevron" aria-hidden="true" size={16} />
					<span className="disclosure-title">{title}</span>
					{summary ? <span className="disclosure-summary">{summary}</span> : null}
				</button>
			</h3>
			<div
				id={regionId}
				role="region"
				aria-labelledby={triggerId}
				className="disclosure-region"
				hidden={!open}
			>
				<div className="disclosure-inner">{children}</div>
			</div>
		</div>
	);
}
