// uvicorn closes idle keep-alive sockets after 5 s (`timeout_keep_alive`), while route.fetch reuses
// its Node-side connection pool across tests in a worker. A request can land on a socket the server
// is closing and fail with ECONNRESET (ci-hub build #30, thread-inspector.spec.js:280). GET is
// idempotent, so it is resent once; other methods keep Playwright's default of no retries.
export function fetchThrough(route) {
	return route.fetch(route.request().method() === 'GET' ? { maxRetries: 1 } : undefined);
}
