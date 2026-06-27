/**
 * Toast notifications: a provider that owns the queue and renders a portalled viewport,
 * plus the `useToast` hook to push notifications from anywhere. Each toast auto-dismisses
 * after `durationMs` (default 5s; pass 0 to keep it until dismissed) and is announced via
 * an `aria-live` region.
 * @author Rodrigo Mason
 */

import type { ReactNode } from 'react';
import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useI18n } from '../../i18n/I18nProvider';
import { IconButton } from './IconButton';

export type ToastTone = 'info' | 'ok' | 'warn' | 'danger';

export interface ToastOptions {
	title: string;
	body?: string;
	tone?: ToastTone;
	/** Auto-dismiss delay in ms; 0 keeps the toast until the user dismisses it. */
	durationMs?: number;
}

interface ToastEntry {
	id: number;
	title: string;
	body?: string;
	tone: ToastTone;
}

interface ToastApi {
	notify: (options: ToastOptions) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** Push toast notifications. Must be called within a {@link ToastProvider}. */
export function useToast(): ToastApi {
	const context = useContext(ToastContext);
	if (!context) {
		throw new Error('useToast must be used within a <ToastProvider>');
	}
	return context;
}

export function ToastProvider({ children }: { children: ReactNode }) {
	const { t } = useI18n();
	const [toasts, setToasts] = useState<ToastEntry[]>([]);
	const counter = useRef(0);

	const dismiss = useCallback((id: number) => {
		setToasts((list) => list.filter((toast) => toast.id !== id));
	}, []);

	const notify = useCallback<ToastApi['notify']>(
		({ title, body, tone = 'info', durationMs = 5000 }) => {
			counter.current += 1;
			const id = counter.current;
			setToasts((list) => [...list, { id, title, body, tone }]);
			if (durationMs > 0) {
				window.setTimeout(() => dismiss(id), durationMs);
			}
		},
		[dismiss],
	);

	const api = useMemo<ToastApi>(() => ({ notify }), [notify]);

	return (
		<ToastContext.Provider value={api}>
			{children}
			{createPortal(
				<div
					className="toast-viewport"
					role="region"
					aria-label={t('app.global.notifications', 'Notifications')}
					aria-live="polite"
					aria-atomic="false"
				>
					{toasts.map((toast) => (
						<div key={toast.id} className="toast" data-tone={toast.tone}>
							<span className="toast-title">{toast.title}</span>
							{toast.body ? <span className="toast-body">{toast.body}</span> : null}
							<IconButton
								className="toast-dismiss"
								aria-label={t('app.global.dismiss', 'Dismiss')}
								onClick={() => dismiss(toast.id)}
							>
								×
							</IconButton>
						</div>
					))}
				</div>,
				document.body,
			)}
		</ToastContext.Provider>
	);
}
