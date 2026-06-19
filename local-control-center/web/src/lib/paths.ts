/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */

/** Turn a directory-like token ("my-project") into a human label ("My Project"). */
export function prettyNameFromDirectory(value: string): string {
	return value
		.trim()
		.replace(/[_-]+/g, ' ')
		.replace(/\s+/g, ' ')
		.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/** Turn a display name ("My Project") into a filesystem-safe slug ("my-project"). */
export function slugFromName(value: string): string {
	return value
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9._-]+/g, '-')
		.replace(/^-+|-+$/g, '')
		.slice(0, 80);
}

/** Return the final path segment, tolerating both Windows and POSIX separators. */
export function lastPathSegment(value: string): string {
	const segments = value
		.trim()
		.replace(/[\\/]+$/g, '')
		.split(/[\\/]/)
		.filter(Boolean);
	return segments.length ? segments[segments.length - 1] : '';
}

/** Join a base path and a child directory, preserving the base's separator style. */
export function joinLocalPath(basePath: string, directoryName: string): string {
	const base = basePath.trim().replace(/[\\/]+$/g, '');
	const child = directoryName.trim().replace(/^[\\/]+/g, '');
	if (!base) return child;
	const separator = base.includes('\\') && !base.includes('/') ? '\\' : '/';
	return `${base}${separator}${child}`;
}
