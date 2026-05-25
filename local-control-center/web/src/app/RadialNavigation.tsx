import type { ComponentType, CSSProperties } from 'react';
import { useEffect, useMemo, useState } from 'react';
import type { LucideProps } from 'lucide-react';

type IconComponent = ComponentType<LucideProps>;

export type RadialNavigationOption<TPage extends string> = {
	id: string;
	label: string;
	shortLabel?: string;
	ariaLabel?: string;
	description: string;
	page?: TPage;
	icon: IconComponent;
	tone?: 'moss' | 'amber' | 'oxblood' | 'blue' | 'violet';
	onActivate?: () => void;
};

export type RadialNavigationModule<TPage extends string> = {
	id: string;
	title: string;
	ariaLabel: string;
	kicker: string;
	centerLabel: string;
	primaryPage: TPage;
	icon: IconComponent;
	options: Array<RadialNavigationOption<TPage>>;
};

type WheelStyle = CSSProperties & {
	'--wheel-size': string;
	'--option-size': string;
	'--option-count': number;
};

type OptionStyle = CSSProperties & {
	'--angle': string;
	'--counter-angle': string;
};

export function RadialNavigation<TPage extends string>({
	modules,
	currentPage,
	onNavigate,
}: {
	modules: Array<RadialNavigationModule<TPage>>;
	currentPage: TPage;
	onNavigate: (page: TPage) => void;
}) {
	const initialFocusedModule = modules.find((module) => module.options.some((option) => option.page === currentPage))?.id ?? modules[0]?.id ?? '';
	const [focusedModuleId, setFocusedModuleId] = useState(initialFocusedModule);
	const [selectedOptions, setSelectedOptions] = useState<Record<string, string>>({});

	useEffect(() => {
		const module = modules.find((item) => item.options.some((option) => option.page === currentPage));
		if (!module) return;
		const option = module.options.find((item) => item.page === currentPage);
		setFocusedModuleId(module.id);
		if (option) {
			setSelectedOptions((previous) => ({ ...previous, [module.id]: option.id }));
		}
	}, [currentPage, modules]);

	return (
		<nav className="radial-nav-list" aria-label="Primary wheel navigation">
			{modules.map((module) => (
				<RadialWheelModule
					key={module.id}
					module={module}
					currentPage={currentPage}
					focused={focusedModuleId === module.id}
					selectedOptionId={selectedOptions[module.id]}
					onFocus={() => {
						setFocusedModuleId(module.id);
						onNavigate(module.primaryPage);
					}}
					onSelect={(option) => {
						setFocusedModuleId(module.id);
						setSelectedOptions((previous) => ({ ...previous, [module.id]: option.id }));
						if (option.onActivate) {
							option.onActivate();
							return;
						}
						if (option.page) {
							onNavigate(option.page);
						}
					}}
				/>
			))}
		</nav>
	);
}

function RadialWheelModule<TPage extends string>({
	module,
	currentPage,
	focused,
	selectedOptionId,
	onFocus,
	onSelect,
}: {
	module: RadialNavigationModule<TPage>;
	currentPage: TPage;
	focused: boolean;
	selectedOptionId?: string;
	onFocus: () => void;
	onSelect: (option: RadialNavigationOption<TPage>) => void;
}) {
	const selectedOption = useMemo(
		() =>
			module.options.find((option) => option.id === selectedOptionId) ??
			module.options.find((option) => option.page === currentPage) ??
			module.options[0],
		[currentPage, module.options, selectedOptionId],
	);
	const count = module.options.length;
	const dense = count > 10;
	const wheelSize = Math.min(500, Math.max(300, 236 + count * (dense ? 18 : 16)));
	const optionSize = dense ? 54 : count > 7 ? 74 : 82;
	const wheelStyle: WheelStyle = {
		'--wheel-size': `${wheelSize}px`,
		'--option-size': `${optionSize}px`,
		'--option-count': count,
	};
	const Icon = module.icon;

	return (
		<section
			className="wheel-module"
			data-focused={focused ? 'true' : 'false'}
			data-option-count={count}
			role="group"
			aria-label={module.ariaLabel}
		>
			<button className="wheel-module-header" type="button" aria-pressed={focused} aria-label={module.title} onClick={onFocus}>
				<span className="wheel-header-icon" aria-hidden="true">
					<Icon size={18} />
				</span>
				<span>{module.title}</span>
				<span className="wheel-state" aria-hidden="true">{focused ? 'FOCUS' : 'READY'}</span>
			</button>
			<div className="rotary-wheel" data-density={dense ? 'dense' : 'normal'} style={wheelStyle}>
				<div className="wheel-track" aria-hidden="true" />
				<span className="rotor-ring" aria-hidden="true" />
				<span className="finger-stop" aria-hidden="true" />
				<div className="wheel-spokes" aria-hidden="true" />
				<div className="wheel-center">
					<strong>{selectedOption.label}</strong>
					<span>{module.centerLabel}</span>
				</div>
				{module.options.map((option, index) => {
					const angle = -90 + (360 / count) * index;
					const OptionIcon = option.icon;
					const visibleLabel = option.shortLabel ?? option.label;
					const optionStyle: OptionStyle = {
						'--angle': `${angle}deg`,
						'--counter-angle': `${-angle}deg`,
					};
					return (
						<button
							key={option.id}
							className="wheel-option"
							data-tone={option.tone ?? 'blue'}
							type="button"
							style={optionStyle}
							aria-current={option.page === currentPage ? 'page' : undefined}
							aria-label={option.ariaLabel ?? option.label}
							onClick={() => onSelect(option)}
						>
							<OptionIcon size={dense ? 12 : 15} aria-hidden="true" />
							<span>{visibleLabel}</span>
						</button>
					);
				})}
			</div>
			<div className="wheel-detail" aria-live="polite">
				<div className="wheel-detail-kicker">{module.kicker}</div>
				<strong>{selectedOption.label}</strong>
				<p>{selectedOption.description}</p>
			</div>
		</section>
	);
}
