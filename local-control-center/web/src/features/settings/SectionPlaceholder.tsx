/**
 * Honest placeholder for settings sections that are not yet configured
 * in Phase 1. Displays the section title and a clear "not configured yet"
 * message without invented controls or sample values.
 */

import { useI18n } from '../../i18n/I18nProvider';

export interface SectionPlaceholderProps {
	titleKey: string;
	titleFallback: string;
}

/** Renders a visually distinct placeholder for a section without Phase-1 settings wired. */
export function SectionPlaceholder({ titleKey, titleFallback }: SectionPlaceholderProps) {
	const { t } = useI18n();
	return (
		<div className="section-placeholder" aria-label={t(titleKey, titleFallback)}>
			<h3 className="section-placeholder-title">{t(titleKey, titleFallback)}</h3>
			<p>
				{t(
					'app.settings.placeholder.notConfigured',
					'This section is not yet configured. Settings will appear here in a future release.',
				)}
			</p>
		</div>
	);
}
