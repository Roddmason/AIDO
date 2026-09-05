import { spawn } from 'node:child_process';
import path from 'node:path';
import { expect, test as base } from '@playwright/test';

// Explicit opt-in for domain/UI tests. HTTP still returns 202; only fixture requests receive
// the actual terminal response. UI requests poll normally while a separate OS process completes
// their selected operation. Raw operational-boundary tests deliberately do not import this file.
export { expect };
export const test = base.extend({
	page: async ({ page }, use) => {
		let pending = Promise.resolve();
		const failures = [];
		const completed = new Map();
		const execute = (id) => {
			if (!completed.has(id)) {
				pending = pending.then(() => runOperation(id));
				completed.set(id, pending);
				pending.catch((error) => failures.push(error));
			}
			return completed.get(id);
		};
		const observe = async (response) => {
			if (response.status() !== 202) return;
			try {
				const body = await response.json();
				if (body.executionId && body.operation) await execute(body.executionId);
			} catch (error) { failures.push(error); }
		};
		page.on('response', observe);
		const originals = new Map();
		for (const method of ['post', 'put', 'patch', 'delete']) {
			const original = page.request[method].bind(page.request);
			originals.set(method, original);
			page.request[method] = async (...args) => {
				const response = await original(...args);
				if (response.status() !== 202) return response;
				const body = await response.json();
				if (!body.executionId || !body.operation) return response;
				await execute(body.executionId);
				const terminal = await (await page.request.get(`/api/v1/executions/${body.executionId}`)).json();
				expect(['completed', 'blocked', 'failed', 'cancelled']).toContain(terminal.status);
				const status = terminal.resultStatusCode || 500;
				const result = terminal.result || { detail: terminal.reason };
				return new Proxy(response, { get(target, key) {
					if (key === 'status') return () => status;
					if (key === 'ok') return () => status >= 200 && status < 300;
					if (key === 'json') return async () => result;
					if (key === 'text') return async () => JSON.stringify(result);
					const value = target[key];
					return typeof value === 'function' ? value.bind(target) : value;
				} });
			};
		}
		try { await use(page); }
		finally {
			page.off('response', observe);
			for (const [method, original] of originals) page.request[method] = original;
			await pending;
			if (failures.length) throw failures[0];
		}
	},
});

function runOperation(id) {
	const db = process.env.PLAYWRIGHT_DB_PATH;
	if (!db) throw new Error('Domain operation fixture requires PLAYWRIGHT_DB_PATH from the web runner');
	const python = path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
	return new Promise((resolve, reject) => {
		const child = spawn(python, ['-m', 'tests_web.fixtures.complete_operation', '--db', db, '--execution-id', id], {
			stdio: ['ignore', 'inherit', 'inherit'],
		});
		child.once('error', reject);
		child.once('exit', (code) => code === 0 ? resolve() : reject(new Error(`Domain operation fixture exited ${code}: ${id}`)));
	});
}
