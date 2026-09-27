/**
 * "3 hours ago" in the UI language for a timestamp, shared by the Home project cards and the
 * Projects lanes. The unit arithmetic lives in the pure homeModel helper; this hook only formats.
 * @author Rodrigo Mason
 */
import { useI18n } from '../../i18n/I18nProvider';
import { relativeTimeParts } from './homeModel';

/** Localised relative time, or null when the timestamp cannot be read or formatted. */
export function useRelativeTime(iso: string): string | null {
	const { language } = useI18n();
	const parts = relativeTimeParts(iso);
	if (!parts) return null;
	try {
		return new Intl.RelativeTimeFormat(language, { numeric: 'auto' }).format(
			parts.value,
			parts.unit,
		);
	} catch {
		return null;
	}
}
