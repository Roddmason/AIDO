/**
 * Browser entrypoint: imports the design-system CSS and mounts the React app.
 *
 * Applies the persisted theme before the first paint and wraps the app in `I18nProvider`
 * (runtime copy) plus the Motion providers: `LazyMotion`/`domMax` in `strict` mode
 * (lazy-loaded features incl. layout/shared-layout for layoutId indicators and reveal/collapse;
 * forbids the heavy `motion.*` API) and `MotionConfig reducedMotion="user"` (declarative,
 * accessible reduced-motion fallback).
 * @author Rodrigo Mason
 */
import './design-system/tokens.css';
import './design-system/base.css';
import './design-system/layout.css';
import './design-system/components.css';
import './design-system/ui.css';
import './design-system/motion.css';

import { domMax, LazyMotion, MotionConfig } from 'motion/react';
import React from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './app/App';
import { ToastProvider } from './components/ui';
import { applyStoredDensity } from './hooks/useDensity';
import { applyStoredTheme } from './hooks/useTheme';
import { I18nProvider } from './i18n/I18nProvider';

applyStoredTheme();
applyStoredDensity();

createRoot(document.getElementById('root') as HTMLElement).render(
	<React.StrictMode>
		<I18nProvider>
			<LazyMotion features={domMax} strict>
				<MotionConfig reducedMotion="user">
					<ToastProvider>
						<App />
					</ToastProvider>
				</MotionConfig>
			</LazyMotion>
		</I18nProvider>
	</React.StrictMode>,
);
