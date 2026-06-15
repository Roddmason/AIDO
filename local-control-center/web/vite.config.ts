import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig({
	root: resolve(__dirname),
	plugins: [react()],
	build: {
		outDir: resolve(__dirname, '../dist/web'),
		emptyOutDir: true,
		assetsDir: 'assets',
		sourcemap: false,
		rollupOptions: {
			output: {
				// Split heavy third-party libraries into cacheable vendor chunks so no single
				// chunk crosses the 500 kB warning threshold, and returning visitors re-download
				// only the app code that actually changed.
				manualChunks(id) {
					if (!id.includes('node_modules')) return undefined;
					if (id.includes('@xyflow') || id.includes('node_modules/d3-')) return 'vendor-flow';
					if (id.includes('lucide-react')) return 'vendor-icons';
					if (
						id.includes('@radix-ui') ||
						id.includes('@floating-ui') ||
						id.includes('react-remove-scroll') ||
						id.includes('aria-hidden')
					) {
						return 'vendor-radix';
					}
					if (
						id.includes('node_modules/react-dom/') ||
						id.includes('node_modules/react/') ||
						id.includes('node_modules/scheduler/')
					) {
						return 'vendor-react';
					}
					return 'vendor';
				},
			},
		},
	},
	server: {
		host: '127.0.0.1',
		port: 4311,
		proxy: {
			'/api': 'http://127.0.0.1:4310',
			'/healthz': 'http://127.0.0.1:4310',
		},
	},
});
