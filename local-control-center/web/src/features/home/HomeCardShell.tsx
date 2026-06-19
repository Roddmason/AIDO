/**
 * Shared chrome for every Home gallery card: the clickable card button and its
 * header row, kept in one place so the four card kinds share identical layout,
 * focus/selection affordances and a11y wiring instead of duplicating boilerplate.
 */
import type { ReactNode } from 'react';

import type { HomeCardItem } from './homeModel';

/**
 * Shared clickable shell for every gallery card: a full-card button with a
 * kind hook (`data-kind`) for scannability, selection state and an accessible
 * label. Keeps the four card components free of duplicated button boilerplate.
 */
export function HomeCard({
	kind,
	onClick,
	selected,
	ariaLabel,
	children,
}: {
	kind: HomeCardItem['kind'];
	onClick: () => void;
	selected?: boolean;
	ariaLabel: string;
	children: ReactNode;
}) {
	return (
		<button
			type="button"
			className="home-card"
			data-kind={kind}
			data-selected={selected ? 'true' : undefined}
			onClick={onClick}
			aria-label={ariaLabel}
		>
			{children}
		</button>
	);
}

/** Card header row: a kind glyph + label on the left, a status badge on the right. */
export function CardHead({
	icon,
	label,
	badge,
}: {
	icon: ReactNode;
	label: string;
	badge: ReactNode;
}) {
	return (
		<span className="home-card-head">
			<span className="home-card-kind">
				{icon}
				<span>{label}</span>
			</span>
			{badge}
		</span>
	);
}
