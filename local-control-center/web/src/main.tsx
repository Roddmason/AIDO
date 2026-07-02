/**
 * Browser entrypoint: imports the design-system CSS and mounts the React app.
 *
 * Applies the persisted theme before the first paint and wraps the app in `I18nProvider`
 * (runtime copy) plus `MotionProvider` (`LazyMotion`/`domMax` strict + `MotionConfig` whose
 * `reducedMotion` follows the user's persisted motion preference, falling back to the OS
 * media query).
 * @author Rodrigo Mason
 */
import './design-system/tokens.css';
import './design-system/base.css';
import './design-system/layout.css';
import './design-system/components.css';
import './design-system/ui.css';
import './design-system/settings.css';
import './design-system/inspector.css';
import './design-system/motion.css';

import React from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './app/App';
import { ToastProvider } from './components/ui';
import { applyStoredDensity } from './hooks/useDensity';
import { applyStoredTheme } from './hooks/useTheme';
import { I18nProvider } from './i18n/I18nProvider';
import { MotionProvider } from './motion/MotionProvider';

applyStoredTheme();
applyStoredDensity();

createRoot(document.getElementById('root') as HTMLElement).render(
	<React.StrictMode>
		<I18nProvider>
			<MotionProvider>
				<ToastProvider>
					<App />
				</ToastProvider>
			</MotionProvider>
		</I18nProvider>
	</React.StrictMode>,
);
