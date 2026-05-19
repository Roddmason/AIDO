import '@xyflow/react/dist/style.css';
import './design-system/tokens.css';
import './design-system/base.css';
import './design-system/layout.css';
import './design-system/components.css';
import './design-system/motion.css';

import React from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './app/App';

createRoot(document.getElementById('root') as HTMLElement).render(
	<React.StrictMode>
		<App />
	</React.StrictMode>,
);
