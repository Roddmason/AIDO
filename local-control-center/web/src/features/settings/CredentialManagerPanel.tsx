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
import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react';

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
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import { Button, SelectField, TextField, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';

type PendingAction = 'load' | 'create' | 'validate' | 'rotate' | 'delete' | 'migrate' | null;

const DEFAULT_FORM = {
	name: '',
	backendKind: 'keyring',
	locator: '',
	authMode: 'token',
	value: '',
};

export function CredentialManagerPanel({ token }: { token: string }) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [credentials, setCredentials] = useState<Credential[]>([]);
	const [backends, setBackends] = useState<CredentialBackend[]>([]);
	const [audit, setAudit] = useState<CredentialAudit[]>([]);
	const [form, setForm] = useState(DEFAULT_FORM);
	const [selectedCredentialId, setSelectedCredentialId] = useState('');
	const [rotateValue, setRotateValue] = useState('');
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
				backendKind: nextWritableBackends.some((backend) => backend.kind === current.backendKind)
					? current.backendKind
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

	const notifyResult = (title: string, body?: string) => notify({ title, body, tone: 'ok' });
	const notifyError = (err: unknown) =>
		notify({
			title: t('settings.credentials.operationFailed', 'Credential operation failed'),
			body: err instanceof Error ? err.message : String(err),
			tone: 'danger',
		});

	const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!token || pending) return;
		setPending('create');
		try {
			await createCredential(token, {
				name: form.name.trim(),
				backendKind: form.backendKind,
				locator: form.locator.trim(),
				authMode: form.authMode.trim() || 'token',
				value: form.value,
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
			notifyResult(
				result.validation.valid
					? t('settings.credentials.valid', 'Credential valid')
					: t('settings.credentials.invalid', 'Credential invalid'),
			);
		} catch (err) {
			notifyError(err);
		} finally {
			setPending(null);
		}
	};

	const handleRotate = async () => {
		if (!token || !selectedCredential || !rotateValue || pending) return;
		setPending('rotate');
		try {
			await rotateCredential(token, selectedCredential.id, { value: rotateValue });
			setRotateValue('');
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
		if (
			!window.confirm(t('settings.credentials.deleteConfirm', 'Delete this credential reference?'))
		) {
			return;
		}
		setPending('delete');
		try {
			await deleteCredential(token, credential.id);
			await refresh();
			notifyResult(t('settings.credentials.deleted', 'Credential deleted'));
		} catch (err) {
			notifyError(err);
		} finally {
			setPending(null);
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
	const selectedBackendWritable = writableBackends.some(
		(backend) => backend.kind === form.backendKind,
	);
	const canCreate = Boolean(
		form.name.trim() && form.locator.trim() && form.value && selectedBackendWritable,
	);
	const canRotate = Boolean(selectedCredential && rotateValue);

	return (
		<section className="stack compact" aria-labelledby="credential-manager-title">
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

			{error ? <p className="field-error">{error}</p> : null}

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
					label={t('settings.credentials.name', 'Name')}
					value={form.name}
					onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
					required
				/>
				<SelectField
					label={t('settings.credentials.backend', 'Backend')}
					value={form.backendKind}
					onChange={(event) =>
						setForm((current) => ({ ...current, backendKind: event.target.value }))
					}
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
					label={t('settings.credentials.locator', 'Locator')}
					value={form.locator}
					onChange={(event) => setForm((current) => ({ ...current, locator: event.target.value }))}
					required
				/>
				<TextField
					label={t('settings.credentials.authMode', 'Auth mode')}
					value={form.authMode}
					onChange={(event) => setForm((current) => ({ ...current, authMode: event.target.value }))}
					required
				/>
				<TextField
					label={t('settings.credentials.secretInput', 'Credential')}
					type="password"
					value={form.value}
					autoComplete="new-password"
					onChange={(event) => setForm((current) => ({ ...current, value: event.target.value }))}
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
					{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => row.name },
					{
						key: 'backend',
						label: t('settings.credentials.backend', 'Backend'),
						render: (row) => <span className="mono">{row.backendKind}</span>,
					},
					{
						key: 'locator',
						label: t('settings.credentials.locator', 'Locator'),
						render: (row) => <span className="mono">{row.locator}</span>,
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
									onClick={() => void handleDelete(row)}
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
							{credential.name}
						</option>
					))}
				</SelectField>
				<TextField
					label={t('settings.credentials.newCredential', 'New credential')}
					type="password"
					value={rotateValue}
					autoComplete="new-password"
					onChange={(event) => setRotateValue(event.target.value)}
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
		</section>
	);
}
