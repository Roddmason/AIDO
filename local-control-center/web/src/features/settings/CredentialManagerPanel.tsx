/**
 * CredentialManager settings panel: lists safe credential metadata and performs
 * create/validate/rotate/delete/migrate operations through the generated API client.
 *
 * Secret values are accepted only by the create/rotate forms and are cleared immediately after a
 * request. The rendered tables use `CredentialRecord`/`CredentialAuditRecord`, which intentionally
 * do not carry raw values, salt or hash material.
 * @author Rodrigo Mason
 */

import {
	CheckCircle2,
	KeyRound,
	Plus,
	RefreshCw,
	RotateCw,
	Trash2,
	UploadCloud,
} from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
	createCredential,
	deleteCredential,
	getCredentialAudit,
	getCredentials,
	migrateCredentials,
	rotateCredential,
	validateCredential,
} from '../../api/client';
import type { Credential, CredentialAudit, CredentialBackend } from '../../api/types';
import {
	StatusChip as Badge,
	Button,
	DataTable,
	EmptyState,
	SelectField,
	TextField,
	useToast,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';

type PendingAction = 'load' | 'create' | 'validate' | 'rotate' | 'delete' | 'migrate' | null;

const DEFAULT_FORM = {
	label: '',
	source: 'keyring',
	credentialRef: '',
	authMode: 'token',
};

export function CredentialManagerPanel({ token }: { token: string }) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [credentials, setCredentials] = useState<Credential[]>([]);
	const [backends, setBackends] = useState<CredentialBackend[]>([]);
	const [audit, setAudit] = useState<CredentialAudit[]>([]);
	const [form, setForm] = useState(DEFAULT_FORM);
	const [selectedCredentialId, setSelectedCredentialId] = useState('');
	const secretRef = useRef<HTMLInputElement>(null);
	const rotateRef = useRef<HTMLInputElement>(null);
	const [hasSecret, setHasSecret] = useState(false);
	const [hasRotation, setHasRotation] = useState(false);
	const [removal, setRemoval] = useState<Credential | null>(null);
	const removalRef = useRef<HTMLElement>(null);
	const removalTrigger = useRef<HTMLElement | null>(null);
	const [pending, setPending] = useState<PendingAction>('load');
	const [error, setError] = useState('');

	const writableBackends = useMemo(
		() => backends.filter((backend) => backend.configured && !backend.readOnly),
		[backends],
	);
	const selectedCredential = credentials.find(
		(credential) => credential.id === selectedCredentialId,
	);

	const refresh = useCallback(async () => {
		setPending('load');
		setError('');
		try {
			const [credentialPayload, auditPayload] = await Promise.all([
				getCredentials(),
				getCredentialAudit(),
			]);
			setCredentials(credentialPayload.credentials);
			setBackends(credentialPayload.backends);
			setAudit(auditPayload.audit);
			const nextWritableBackends = credentialPayload.backends.filter(
				(backend) => backend.configured && !backend.readOnly,
			);
			const fallbackBackendKind =
				nextWritableBackends.find((backend) => backend.default)?.kind ??
				nextWritableBackends[0]?.kind ??
				'keyring';
			setSelectedCredentialId((current) =>
				current && credentialPayload.credentials.some((item) => item.id === current)
					? current
					: (credentialPayload.credentials[0]?.id ?? ''),
			);
			setForm((current) => ({
				...current,
				source: nextWritableBackends.some((backend) => backend.kind === current.source)
					? current.source
					: fallbackBackendKind,
			}));
		} catch (err) {
			const message = err instanceof Error ? err.message : String(err);
			setError(message);
		} finally {
			setPending(null);
		}
	}, []);

	useEffect(() => {
		void refresh();
	}, [refresh]);

	useEffect(() => {
		if (removal) removalRef.current?.focus();
	}, [removal]);

	const notifyResult = (title: string, body?: string) => notify({ title, body, tone: 'ok' });
	// A failing vault/transport may echo its input. Never put that response into a toast.
	const notifyError = (_err: unknown) =>
		notify({
			title: t('settings.credentials.operationFailed', 'Credential operation failed'),
			body: t(
				'settings.credentials.safeError',
				'The operation could not be completed. Check the backend availability; secret input has been cleared.',
			),
			tone: 'danger',
		});

	const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!token || pending) return;
		const value = secretRef.current?.value ?? '';
		if (secretRef.current) secretRef.current.value = '';
		setHasSecret(false);
		setPending('create');
		try {
			await createCredential(token, {
				label: form.label.trim(),
				source: form.source,
				credentialRef: form.credentialRef.trim(),
				authMode: form.authMode.trim() || 'token',
				value,
			});
			setForm(DEFAULT_FORM);
			await refresh();
			notifyResult(t('settings.credentials.created', 'Credential added'));
		} catch (err) {
			notifyError(err);
		} finally {
			setPending(null);
		}
	};

	const handleValidate = async (credentialId: string) => {
		if (!token || pending) return;
		setPending('validate');
		try {
			const result = await validateCredential(token, credentialId);
			await refresh();
			notify({
				title: result.validation.valid
					? t('settings.credentials.valid', 'Credential valid')
					: t('settings.credentials.invalid', 'Credential invalid'),
				tone: result.validation.valid ? 'ok' : 'danger',
			});
		} catch (err) {
			notifyError(err);
		} finally {
			setPending(null);
		}
	};

	const handleRotate = async () => {
		if (!token || !selectedCredential || !hasRotation || pending) return;
		const value = rotateRef.current?.value ?? '';
		if (rotateRef.current) rotateRef.current.value = '';
		setHasRotation(false);
		setPending('rotate');
		try {
			await rotateCredential(token, selectedCredential.id, { value });
			await refresh();
			notifyResult(t('settings.credentials.rotated', 'Credential rotated'));
		} catch (err) {
			notifyError(err);
		} finally {
			setPending(null);
		}
	};

	const handleDelete = async (credential: Credential) => {
		if (!token || pending) return;
		setPending('delete');
		try {
			await deleteCredential(token, credential.id);
			await refresh();
			notifyResult(t('settings.credentials.deleted', 'Credential deleted'));
		} catch (err) {
			notifyError(err);
		} finally {
			setPending(null);
			setRemoval(null);
			removalTrigger.current?.focus();
		}
	};

	const handleMigrate = async () => {
		if (!token || pending) return;
		setPending('migrate');
		try {
			const result = await migrateCredentials(token);
			await refresh();
			const count = Array.isArray(result.report.credentials) ? result.report.credentials.length : 0;
			notifyResult(t('settings.credentials.migrated', 'Environment migrated'), String(count));
		} catch (err) {
			notifyError(err);
		} finally {
			setPending(null);
		}
	};

	const disabled = !token || pending !== null;
	const selectedBackendWritable = writableBackends.some((backend) => backend.kind === form.source);
	const canCreate = Boolean(
		form.label.trim() && form.credentialRef.trim() && hasSecret && selectedBackendWritable,
	);
	const canRotate = Boolean(selectedCredential && hasRotation);

	return (
		<section
			className="credential-manager stack compact"
			aria-labelledby="credential-manager-title"
		>
			<div className="surface-toolbar">
				<div className="inline">
					<KeyRound aria-hidden="true" size={17} />
					<h3 id="credential-manager-title" className="surface-title">
						{t('settings.credentials.title', 'Credential Manager')}
					</h3>
				</div>
				<div className="inline">
					<Button onClick={() => void refresh()} disabled={disabled} icon={<RefreshCw size={15} />}>
						{t('settings.credentials.refresh', 'Refresh credentials')}
					</Button>
					<Button
						onClick={() => void handleMigrate()}
						disabled={disabled}
						loading={pending === 'migrate'}
						icon={<UploadCloud size={15} />}
					>
						{t('settings.credentials.migrate', 'Migrate env')}
					</Button>
				</div>
			</div>

			<p className="field-help">
				{t(
					'settings.credentials.boundaries',
					'Global credential references · saving does not authenticate a provider, choose a model or run inference. CLI subscription sessions are managed separately under Providers & CLI.',
				)}
			</p>
			{error ? (
				<p className="field-error" role="alert">
					{error}
				</p>
			) : null}

			<div className="inline">
				{backends.map((backend) => (
					<Badge key={backend.kind} tone={backend.configured ? 'ok' : 'warn'}>
						{backend.kind}
						{backend.default ? ' *' : ''}
					</Badge>
				))}
			</div>

			<form className="form-grid" onSubmit={(event) => void handleCreate(event)}>
				<TextField
					label={t('settings.credentials.label', 'Label')}
					value={form.label}
					onChange={(event) => setForm((current) => ({ ...current, label: event.target.value }))}
					required
				/>
				<SelectField
					label={t('settings.credentials.source', 'Source')}
					value={form.source}
					onChange={(event) => setForm((current) => ({ ...current, source: event.target.value }))}
					required
				>
					{backends.map((backend) => (
						<option
							key={backend.kind}
							value={backend.kind}
							disabled={!backend.configured || backend.readOnly}
						>
							{backend.kind}
						</option>
					))}
				</SelectField>
				<TextField
					label={t('settings.credentials.credentialRef', 'Credential ref')}
					value={form.credentialRef}
					onChange={(event) =>
						setForm((current) => ({ ...current, credentialRef: event.target.value }))
					}
					required
				/>
				<TextField
					label={t('settings.credentials.authMode', 'Auth mode')}
					value={form.authMode}
					onChange={(event) => setForm((current) => ({ ...current, authMode: event.target.value }))}
					required
				/>
				<TextField
					ref={secretRef}
					label={t('settings.credentials.secretInput', 'Credential')}
					type="password"
					autoComplete="new-password"
					onChange={(event) => setHasSecret(Boolean(event.target.value))}
					required
				/>
				<Button
					className="settings-action"
					type="submit"
					variant="primary"
					disabled={disabled || !canCreate}
					loading={pending === 'create'}
					icon={<Plus size={15} />}
				>
					{t('settings.credentials.add', 'Add')}
				</Button>
			</form>

			{removal ? (
				<section
					ref={removalRef}
					tabIndex={-1}
					className="panel stack compact"
					aria-label={t('settings.credentials.deleteConfirm', 'Delete this credential reference?')}
					onKeyDown={(event) => {
						if (event.key === 'Escape') {
							event.stopPropagation();
							setRemoval(null);
							removalTrigger.current?.focus();
						}
					}}
				>
					<h4>{t('settings.credentials.deleteConfirm', 'Delete this credential reference?')}</h4>
					<p>
						{removal.label} · <span className="mono">{removal.credentialRef}</span>
					</p>
					<div className="inline">
						<Button
							disabled={disabled}
							onClick={() => {
								setRemoval(null);
								removalTrigger.current?.focus();
							}}
						>
							{t('app.workspace.action.cancel', 'Cancel')}
						</Button>
						<Button variant="danger" disabled={disabled} onClick={() => void handleDelete(removal)}>
							{t('settings.credentials.confirmDelete', 'Delete reference')}
						</Button>
					</div>
				</section>
			) : null}

			<DataTable
				rows={credentials}
				caption={t('settings.credentials.table', 'Credentials')}
				empty={
					<EmptyState
						title={t('settings.credentials.emptyTitle', 'No credentials')}
						body={t('settings.credentials.emptyBody', 'Add a credential to enable managed refs.')}
					/>
				}
				columns={[
					{
						key: 'label',
						label: t('settings.credentials.label', 'Label'),
						render: (row) => row.label,
					},
					{
						key: 'source',
						label: t('settings.credentials.source', 'Source'),
						render: (row) => <span className="mono">{row.source}</span>,
					},
					{
						key: 'credentialRef',
						label: t('settings.credentials.credentialRef', 'Credential ref'),
						render: (row) => <span className="mono">{row.credentialRef}</span>,
					},
					{
						key: 'providers',
						label: t('settings.credentials.providers', 'Providers'),
						render: (row) =>
							row.providerUsages.length ? (
								<div className="inline">
									{row.providerUsages.map((usage) => (
										<Badge key={usage.providerId} tone={usage.enabled ? 'ok' : 'warn'}>
											{usage.displayName || usage.providerId}
										</Badge>
									))}
								</div>
							) : (
								<span className="muted">{t('settings.credentials.noProviders', 'None')}</span>
							),
					},
					{
						key: 'status',
						label: t('ui.static.status.bae7d5be', 'Status'),
						render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge>,
					},
					{
						key: 'validated',
						label: t('settings.credentials.validated', 'Validated'),
						render: (row) => <span className="mono">{row.lastValidatedAt ?? '-'}</span>,
					},
					{
						key: 'actions',
						label: t('settings.credentials.actions', 'Actions'),
						render: (row) => (
							<div className="inline">
								<Button
									onClick={() => void handleValidate(row.id)}
									disabled={disabled}
									icon={<CheckCircle2 size={14} />}
								>
									{t('settings.credentials.validate', 'Validate')}
								</Button>
								<Button
									variant="danger"
									onClick={(event) => {
										removalTrigger.current = event.currentTarget;
										setRemoval(row);
									}}
									disabled={disabled}
									icon={<Trash2 size={14} />}
								>
									{t('settings.credentials.delete', 'Delete')}
								</Button>
							</div>
						),
					},
				]}
			/>

			<details className="disclosure">
				<summary>{t('settings.credentials.advanced', 'Advanced · rotation and audit')}</summary>
				<div className="form-grid">
					<SelectField
						label={t('settings.credentials.rotateTarget', 'Rotate target')}
						value={selectedCredentialId}
						onChange={(event) => setSelectedCredentialId(event.target.value)}
						disabled={!credentials.length}
					>
						{credentials.length ? null : (
							<option value="">{t('settings.credentials.noTarget', 'No credential')}</option>
						)}
						{credentials.map((credential) => (
							<option key={credential.id} value={credential.id}>
								{credential.label}
							</option>
						))}
					</SelectField>
					<TextField
						ref={rotateRef}
						label={t('settings.credentials.newCredential', 'New credential')}
						type="password"
						autoComplete="new-password"
						onChange={(event) => setHasRotation(Boolean(event.target.value))}
					/>
					<Button
						className="settings-action"
						onClick={() => void handleRotate()}
						disabled={disabled || !canRotate}
						loading={pending === 'rotate'}
						icon={<RotateCw size={15} />}
					>
						{t('settings.credentials.rotate', 'Rotate')}
					</Button>
				</div>

				<DataTable
					rows={audit.slice(-8).reverse()}
					caption={t('settings.credentials.audit', 'Credential audit')}
					empty={
						<EmptyState
							title={t('settings.credentials.auditEmptyTitle', 'No credential audit')}
							body={t('settings.credentials.auditEmptyBody', 'Credential operations appear here.')}
						/>
					}
					columns={[
						{
							key: 'action',
							label: t('settings.credentials.action', 'Action'),
							render: (row) => <span className="mono">{row.action}</span>,
						},
						{
							key: 'outcome',
							label: t('settings.credentials.outcome', 'Outcome'),
							render: (row) => <Badge tone={toneForStatus(row.outcome)}>{row.outcome}</Badge>,
						},
						{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => row.name },
						{
							key: 'backend',
							label: t('settings.credentials.backend', 'Backend'),
							render: (row) => <span className="mono">{row.backendKind}</span>,
						},
						{
							key: 'created',
							label: t('settings.credentials.createdAt', 'Created'),
							render: (row) => <span className="mono">{row.createdAt}</span>,
						},
					]}
				/>
			</details>
		</section>
	);
}
