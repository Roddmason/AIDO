import React, { useState } from 'react';
import { Database, Search } from 'lucide-react';

import { createMemory, reindexRetrieval, searchRetrieval } from '../../api/platform-api.js';
import { Badge, Button, DataTable, EmptyState, Surface } from '../../components/primitives.jsx';
import { asArray, formatDate, shortId } from '../common/format.js';

export function MemoryRetrievalPage({ data }) {
	const overview = data.overview || {};
	const retrieval = data.retrievalStatus || {};
	const projects = asArray(overview.projects);
	const memoryItems = asArray(overview.memoryItems);
	const [query, setQuery] = useState('');
	const [content, setContent] = useState('');
	const [results, setResults] = useState([]);
	const [searching, setSearching] = useState(false);

	const projectId = projects[0]?.id;

	async function runSearch(event) {
		event.preventDefault();
		setSearching(true);
		try {
			const payload = await searchRetrieval({ query, limit: 8 });
			setResults(asArray(payload.results));
		} finally {
			setSearching(false);
		}
	}

	async function saveMemory(event) {
		event.preventDefault();
		if (!projectId || !content.trim()) return;
		await data.mutate((token) =>
			createMemory(token, {
				projectId,
				scope: 'project',
				scopeId: projectId,
				kind: 'note',
				content,
				sourceRef: 'web.memory',
			}),
		);
		setContent('');
	}

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Memory/Retrieval</p>
				<h1 className="editorial-title">SQLite remains truth; vector search is a rebuildable index.</h1>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface title="Retrieval Backend" kicker="FAISS Status">
					<div className="timeline">
						<div className="timeline-item">
							<Database size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>{retrieval.backend || 'not reported'}</strong>
								<span className="muted">{retrieval.degraded ? 'degraded fallback is visible' : 'normal mode'}</span>
							</div>
						</div>
						<div className="timeline-item">
							<Search size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>{retrieval.indexedItems || 0} indexed items</strong>
								<span className="muted">dimension {retrieval.dimensions || 'not indexed'}</span>
							</div>
						</div>
					</div>
				</Surface>
				<Surface
					title="Index Controls"
					kicker="Lifecycle"
					actions={<Badge tone={retrieval.degraded ? 'oxblood' : 'moss'} status={retrieval.degraded ? 'degraded' : 'ok'}>{retrieval.degraded ? 'degraded' : 'ready'}</Badge>}
				>
					<div className="form-grid">
						<p className="muted">Reindex rebuilds vector state from canonical SQLite memory records.</p>
						<Button onClick={() => data.mutate((token) => reindexRetrieval(token))} disabled={data.busy}>
							Reindex
						</Button>
					</div>
				</Surface>
			</div>
			<div className="split-pane" data-motion-item>
				<Surface title="Semantic Search" kicker="Query">
					<form className="form-grid" onSubmit={runSearch}>
						<div className="field">
							<label htmlFor="retrieval-query">Search memory</label>
							<input id="retrieval-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find decisions, runs, sources..." />
						</div>
						<Button type="submit" disabled={searching || !query.trim()}>
							Search
						</Button>
					</form>
					<div style={{ marginTop: 'var(--space-md)' }}>
						<DataTable
							rows={results}
							empty={<EmptyState title="No search results yet" body="Run a query after indexing memory." />}
							columns={[
								{ key: 'score', label: 'Score', render: (row) => <code>{Number(row.score || 0).toFixed(3)}</code> },
								{ key: 'content', label: 'Content', render: (row) => row.item?.content || row.content || 'not recorded' },
								{ key: 'sourceRef', label: 'Source', render: (row) => row.item?.sourceRef || row.sourceRef || 'unknown' },
							]}
						/>
					</div>
				</Surface>
				<Surface title="Add Memory" kicker="Canonical Write">
					<form className="form-grid" onSubmit={saveMemory}>
						<div className="field">
							<label htmlFor="memory-content">Memory content</label>
							<textarea id="memory-content" value={content} onChange={(event) => setContent(event.target.value)} placeholder="Record a decision, constraint or source reference." />
						</div>
						<Button type="submit" disabled={!projectId || !content.trim() || data.busy}>
							Save memory
						</Button>
						{!projectId ? <p className="muted" role="alert">No project is available yet.</p> : null}
					</form>
				</Surface>
			</div>
			<Surface title="Memory Records" kicker="Versions">
				<DataTable
					rows={memoryItems}
					empty={<EmptyState title="No memory records" body="Memory created by workers or the web UI will be versioned here." />}
					columns={[
						{ key: 'scope', label: 'Scope' },
						{ key: 'kind', label: 'Kind' },
						{ key: 'version', label: 'Version' },
						{ key: 'hash', label: 'Hash', render: (row) => <code>{shortId(row.hash)}</code> },
						{ key: 'sourceRef', label: 'Source' },
						{ key: 'createdAt', label: 'Created', render: (row) => formatDate(row.createdAt) },
					]}
				/>
			</Surface>
		</div>
	);
}
