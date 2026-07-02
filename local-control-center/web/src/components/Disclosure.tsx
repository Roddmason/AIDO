/**
 * Accessible collapsible-section primitive used for progressive disclosure in Settings.
 *
 * Keeps the WAI-ARIA disclosure wiring (heading-level trigger, `aria-expanded`,
 * controlled region) in one place so feature panels can hide dense content safely.
 * @author Rodrigo Mason
 */
import { ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';
import { useId, useState } from 'react';

/**
 * Accessible disclosure for a single collapsible section. Keeps dense tables and
 * advanced options behind progressive disclosure inside the Settings group cards.
 *
 * Each instance owns its open state (independent — several can be open at once).
 * The trigger lives inside a heading whose level matches the surrounding outline
 * (`headingLevel`, default <h3> for Settings: h1 page → h2 group → h3 section;
 * panels whose sections already sit under an h4 pass 4). The panel uses `hidden`
 * when collapsed so it leaves the tab order and the accessibility tree; the open
 * transition is handled in CSS and is neutralised under prefers-reduced-motion.
 */
export function Disclosure({
	title,
	children,
	summary,
	defaultOpen = false,
	headingLevel = 3,
}: {
	title: string;
	children: ReactNode;
	summary?: ReactNode;
	defaultOpen?: boolean;
	/** Heading level for the trigger, matching the host panel's outline. */
	headingLevel?: 3 | 4;
}) {
	const reactId = useId();
	const triggerId = `disclosure-trigger-${reactId}`;
	const regionId = `disclosure-region-${reactId}`;
	const [open, setOpen] = useState(defaultOpen);
	const Heading = headingLevel === 4 ? 'h4' : 'h3';
	return (
		<div className="disclosure" data-open={open}>
			<Heading className="disclosure-heading">
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
			</Heading>
			<section
				id={regionId}
				aria-labelledby={triggerId}
				className="disclosure-region"
				hidden={!open}
			>
				<div className="disclosure-inner">{children}</div>
			</section>
		</div>
	);
}
