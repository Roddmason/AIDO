/**
 * Toast notifications: a provider that owns the queue and renders a portalled viewport,
 * plus the `useToast` hook to push notifications from anywhere. Each toast auto-dismisses
 * after `durationMs` (default 5s; pass 0 to keep it until dismissed) and is announced via
 * an `aria-live` region.
 * @author Rodrigo Mason
 */

import type { ReactNode } from 'react';
import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useRef,
	useState,
} from 'react';
import { createPortal } from 'react-dom';

import { useI18n } from '../../i18n/I18nProvider';
import { IconButton } from './IconButton';

export type ToastTone = 'info' | 'ok' | 'warn' | 'danger';

export interface ToastAction {
	label: string;
	/** Runs when the user presses the action; the toast dismisses itself right after. */
	onPress: () => void;
}

export interface ToastOptions {
	title: string;
	body?: string;
	tone?: ToastTone;
	/** Auto-dismiss delay in ms; 0 keeps the toast until the user dismisses it. */
	durationMs?: number;
	/** Optional inline action (e.g. an undo) rendered next to the dismiss button. */
	action?: ToastAction;
}

interface ToastEntry {
	id: number;
	title: string;
	body?: string;
	tone: ToastTone;
	action?: ToastAction;
	/** Original auto-dismiss delay, re-armed after a hover/focus pause; 0 means manual dismiss. */
	durationMs: number;
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
	const timers = useRef(new Map<number, number>());

	const clearTimer = useCallback((id: number) => {
		const timer = timers.current.get(id);
		if (timer !== undefined) {
			window.clearTimeout(timer);
			timers.current.delete(id);
		}
	}, []);

	const dismiss = useCallback(
		(id: number) => {
			clearTimer(id);
			setToasts((list) => list.filter((toast) => toast.id !== id));
		},
		[clearTimer],
	);

	// Re-armed after each hover/focus pause so the user controls the time limit (WCAG 2.2.1).
	const armDismiss = useCallback(
		(id: number, durationMs: number) => {
			clearTimer(id);
			if (durationMs > 0) {
				timers.current.set(
					id,
					window.setTimeout(() => dismiss(id), durationMs),
				);
			}
		},
		[clearTimer, dismiss],
	);

	useEffect(() => {
		const pending = timers.current;
		return () => {
			for (const timer of pending.values()) window.clearTimeout(timer);
			pending.clear();
		};
	}, []);

	const notify = useCallback<ToastApi['notify']>(
		({ title, body, tone = 'info', durationMs = 5000, action }) => {
			counter.current += 1;
			const id = counter.current;
			setToasts((list) => [...list, { id, title, body, tone, action, durationMs }]);
			armDismiss(id, durationMs);
		},
		[armDismiss],
	);

	const api = useMemo<ToastApi>(() => ({ notify }), [notify]);

	return (
		<ToastContext.Provider value={api}>
			{children}
			{createPortal(
				<section
					className="toast-viewport"
					aria-label={t('app.global.notifications', 'Notifications')}
				>
					{toasts.map((toast) => (
						// Each toast is its own atomic status region so title, body and the optional
						// action are announced together; hover/focus pauses the auto-dismiss timer.
						<div
							key={toast.id}
							className="toast"
							data-tone={toast.tone}
							role="status"
							onMouseEnter={() => clearTimer(toast.id)}
							onMouseLeave={() => armDismiss(toast.id, toast.durationMs)}
							onFocusCapture={() => clearTimer(toast.id)}
							onBlurCapture={() => armDismiss(toast.id, toast.durationMs)}
						>
							<span className="toast-title">{toast.title}</span>
							{toast.body ? <span className="toast-body">{toast.body}</span> : null}
							{toast.action ? (
								<button
									type="button"
									className="toast-action"
									onClick={() => {
										toast.action?.onPress();
										dismiss(toast.id);
									}}
								>
									{toast.action.label}
								</button>
							) : null}
							<IconButton
								className="toast-dismiss"
								aria-label={t('app.global.dismiss', 'Dismiss')}
								onClick={() => dismiss(toast.id)}
							>
								×
							</IconButton>
						</div>
					))}
				</section>,
				document.body,
			)}
		</ToastContext.Provider>
	);
}
