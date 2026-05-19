import React from 'react';
import { Clapperboard, FileCode2 } from 'lucide-react';

import { EmptyState, Surface } from '../../components/primitives.jsx';

export function DesignLabPage({ data }) {
	const openDesign = data.overview?.openDesign || {};

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Design Lab</p>
				<h1 className="editorial-title">HyperFrames and open-design artifacts are a lane, not the product shell.</h1>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface title="Open Design Backend" kicker="Artifacts">
					<div className="timeline">
						<div className="timeline-item">
							<FileCode2 size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>{openDesign.status || 'not exposed'}</strong>
								<span className="muted">runtime {openDesign.runtime || 'not reported'}</span>
							</div>
						</div>
					</div>
				</Surface>
				<Surface title="Motion Artifacts" kicker="HyperFrames">
					<div className="timeline">
						<div className="timeline-item">
							<Clapperboard size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>Semantic motion only</strong>
								<span className="muted">Section changes, new events, approvals and reindex progress may animate.</span>
							</div>
						</div>
					</div>
				</Surface>
			</div>
			<Surface title="Artifact Index" kicker="Read-only">
				<EmptyState title="No artifact endpoint yet" body="Expose a read-only artifact index before rendering files here. The UI will not invent design assets." />
			</Surface>
		</div>
	);
}
