/** Joins truthy class fragments into one className string (no dependency on `clsx`). */
export function cn(...parts: Array<string | false | null | undefined>): string {
	return parts.filter(Boolean).join(' ');
}
