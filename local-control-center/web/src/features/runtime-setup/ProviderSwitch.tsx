/**
 * Switch del operador de un proveedor (`provider_accounts.enabled`): apagado, ningún hilo, agente ni
 * failover lo usa. Un solo marcado para las tarjetas de proveedores, de endpoints Ollama y de endpoints
 * locales, así que las tres se leen y se prueban igual. Queda bloqueado mientras una llamada en curso
 * usa el proveedor, y deshabilitado con una pista cuando todavía no hay cuenta que encender.
 * @author Rodrigo Mason
 */

import { useI18n } from '../../i18n/I18nProvider';

type ProviderSwitchProps = {
	/** Nombre visible del proveedor, parte del nombre accesible del switch. */
	providerName: string;
	checked: boolean;
	/** Otra acción en curso: el switch espera sin cambiar de estado. */
	busy?: boolean;
	/** Una llamada en curso usa el proveedor: no se puede apagar hasta que termine. */
	inUse?: boolean;
	/** No hay cuenta todavía (p. ej. un gateway sin configurar): no hay nada que encender. */
	unavailableHint?: string;
	onChange: (enabled: boolean) => void;
};

export function ProviderSwitch({
	providerName,
	checked,
	busy = false,
	inUse = false,
	unavailableHint,
	onChange,
}: ProviderSwitchProps) {
	const { t } = useI18n();
	const locked = Boolean(inUse && checked);
	const inUseHint = t(
		'app.providers.switch.inUseHint',
		'A running thread is using this provider; you can switch it off when that run finishes.',
	);
	const hint = locked ? inUseHint : unavailableHint;
	const disabled = busy || locked || Boolean(unavailableHint);
	return (
		<label
			className="setting-switch provider-switch"
			data-disabled={disabled ? 'true' : undefined}
			data-in-use={locked ? 'true' : undefined}
			title={hint}
		>
			<input
				type="checkbox"
				checked={checked}
				disabled={disabled}
				aria-label={t('app.providers.switch.label', 'Use {provider} in threads').replace(
					'{provider}',
					providerName,
				)}
				aria-description={hint}
				onChange={(event) => onChange(event.target.checked)}
			/>
			<span className="setting-switch-track" aria-hidden="true">
				<span className="setting-switch-thumb" />
			</span>
			<span className="setting-switch-state" aria-hidden="true">
				{locked
					? t('app.providers.switch.inUse', 'In use')
					: checked
						? t('app.providers.switch.on', 'Active')
						: t('app.providers.switch.off', 'Inactive')}
			</span>
		</label>
	);
}
