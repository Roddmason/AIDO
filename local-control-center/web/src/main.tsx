/**
 * Browser entrypoint: imports the design-system CSS and mounts the React app.
 *
 * Applies the persisted theme before the first paint and wraps the app in
 * `I18nProvider` so every component can resolve copy from the runtime catalog.
 */
import '@xyflow/react/dist/style.css';
import './design-system/tokens.css';
import './design-system/base.css';
import './design-system/layout.css';
import './design-system/components.css';
import './design-system/motion.css';

import React from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './app/App';
import { applyStoredTheme } from './hooks/useTheme';
import { I18nProvider } from './i18n/I18nProvider';

// Apply the persisted theme before the first paint so a stored light preference
// does not flash the dark default on reload.
applyStoredTheme();

createRoot(document.getElementById('root') as HTMLElement).render(
	<React.StrictMode>
		<I18nProvider>
			<App />
		</I18nProvider>
	</React.StrictMode>,
);
