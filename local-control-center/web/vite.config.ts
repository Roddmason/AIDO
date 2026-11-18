import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

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
				// Split heavy vendor libraries into their own chunks so the app chunk stays small and
				// vendor code caches independently across deploys (also clears Vite's chunk-size advisory).
				manualChunks(id) {
					if (!id.includes('node_modules')) return undefined;
					if (id.includes('react')) return 'react';
					if (id.includes('motion')) return 'motion';
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
