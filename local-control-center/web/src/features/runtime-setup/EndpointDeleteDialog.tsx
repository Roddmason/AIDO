/**
 * Diálogo de borrado compartido por los paneles de endpoints locales y de Ollama. Confirma el borrado
 * y, si el backend responde 409 `local_endpoint_in_use`, se convierte en la lista de equipos de hilo y
 * role policies del operador que hay que reasignar antes; AIDO nunca los reasigna solo. Vive en su
 * propio módulo para que el panel de Ollama no arrastre el chunk diferido del panel local.
 * @author Rodrigo Mason
 */

import { useEffect, useState } from 'react';

import { deleteLocalEndpoint } from '../../api/client';
import { StatusChip as Badge, Button, Dialog as Modal } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import { type EndpointReference, endpointInUseReferences, errorMessage } from './localEndpoints';

/**
 * Confirms a deletion; a 409 turns the dialog into the list of teams and policies to reassign. Shared
 * with the Ollama endpoints panel, which passes its own delete call and copy.
 */
export function DeleteEndpointDialog({
	card,
	token,
	onClose,
	onDeleted,
	remove = deleteLocalEndpoint,
	title,
	description,
}: {
	card: { id: string } | null;
	token: string;
	onClose: () => void;
	onDeleted: () => Promise<void>;
	/** The delete call; rejects with the 409 detail while the endpoint is still referenced. */
	remove?: (token: string, endpointId: string) => Promise<void>;
	title?: string;
	description?: string;
}) {
	const { t } = useI18n();
	const [busy, setBusy] = useState(false);
	const [references, setReferences] = useState<EndpointReference[] | null>(null);
	const [error, setError] = useState('');

	useEffect(() => {
		if (!card) return;
		setReferences(null);
		setError('');
	}, [card]);

	if (!card) return null;

	const confirm = async () => {
		if (busy) return;
		setBusy(true);
		setError('');
		try {
			await remove(token, card.id);
			await onDeleted();
		} catch (deleteError) {
			const inUse = endpointInUseReferences(deleteError);
			if (inUse) setReferences(inUse);
			else
				setError(redactVisibleSecret(errorMessage(deleteError), 'local endpoint deletion failed'));
		} finally {
			setBusy(false);
		}
	};

	return (
		<Modal
			open
			label={title ?? t('app.localRuntime.delete.title', 'Delete local endpoint')}
			onClose={onClose}
		>
			<div className="form-grid">
				<p className="field-help">
					{description ??
						t(
							'app.localRuntime.delete.body',
							'Removes the endpoint, its model catalog and its model settings. Usage history, audit and evidence are kept.',
						)}{' '}
					<span className="mono">{card.id}</span>
				</p>
				{references ? (
					<section className="stack compact" role="alert">
						<strong>{t('app.localRuntime.delete.inUse', 'This endpoint is still in use')}</strong>
						<span className="field-help">
							{t(
								'app.localRuntime.delete.inUseHelp',
								'Reassign these first; AIDO never reassigns them for you.',
							)}
						</span>
						<ul className="stack compact">
							{references.map((reference) => (
								<li key={`${reference.kind}:${reference.id}`} className="inline">
									<Badge tone="warn">
										{reference.kind === 'thread_team'
											? t('app.localRuntime.delete.kindThreadTeam', 'Thread team')
											: t('app.localRuntime.delete.kindRolePolicy', 'Role policy')}
									</Badge>
									<span>{reference.label}</span>
								</li>
							))}
						</ul>
					</section>
				) : null}
				{error ? (
					<div className="form-error" role="alert">
						{error}
					</div>
				) : null}
				<div className="wizard-actions">
					<Button onClick={onClose} disabled={busy}>
						{t('app.localRuntime.delete.cancel', 'Cancel')}
					</Button>
					<Button
						variant="danger"
						loading={busy}
						disabled={references !== null}
						onClick={() => void confirm()}
					>
						{t('app.localRuntime.delete.confirm', 'Delete endpoint')}
					</Button>
				</div>
			</div>
		</Modal>
	);
}
