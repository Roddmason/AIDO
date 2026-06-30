# Threads — Transición Depth Dissolve + Layout Sticky (Design Spec)

> Transición visual "Depth dissolve" entre el intake de hilo nuevo y el hilo activo, más
> verificación/ajuste del layout sticky (chat arriba fijo, consola de ejecución con scroll abajo).
> Fecha: 2026-06-30 · Rama: `dev` · Autor: Rodrigo Mason · Solo frontend/visual, sin lógica de negocio.

## 1. Contexto verificado

- Threads reales ya implementadas (ver `2026-06-28-real-threads-design.md`). `ThreadConversation.tsx`
  ya separa dos ramas: `NewThreadComposer` (intake centrado) vs. layout de hilo activo
  (`.thread-live-layout`), donde `.thread-chat-sticky` (título + transcript + decisión + composer)
  usa `position: sticky; top: 0` y `.thread-execution-console` (los "avances": eventos del
  coordinator/agentes) vive debajo y hace scroll dentro de `.thread-live-layout` (`overflow-y: auto`).
- `motion` 12.40.0 ya instalado (`LazyMotion domMax` + `MotionConfig reducedMotion="user"` en
  `main.tsx`), con un sistema de variantes declarativas en `motion/variants.ts` (transforms pequeños +
  opacity, reduced-motion-safe por diseño). `domMax` ya soporta transforms 3D (`rotateX`, `perspective`)
  sin librerías nuevas.
- Lo que falta: (1) confirmar **en navegador real** que el sticky+scroll efectivamente fija el bloque
  título+chat arriba mientras la consola de ejecución se desplaza debajo (verificado por lectura de
  CSS, no en runtime); (2) la transición entre el estado "nuevo hilo" y "hilo activo" hoy es un corte
  duro — cada rama tiene su propio fade-in aislado, sin cross-fade coordinado entre ramas.
- Decisión de estilo de transición validada con el usuario mediante 3 prototipos interactivos
  (depth dissolve / morph+consola / slide-and-stack): se eligió **Depth dissolve**.

## 2. Objetivo verificable

Al crear un hilo desde el composer de intake, el usuario ve una transición "Depth dissolve"
coordinada: el intake se hunde con una leve rotación 3D y se desvanece, el header+chat del hilo
activo entra y se asienta en su posición sticky, los mensajes del transcript aparecen en cascada
corta, y la consola de ejecución ("avances") aparece después, en cascada, con el chat ya fijo
arriba. El resto de cambios de fase (loading→listo, error, cambiar de hilo en el sidebar) usan un
crossfade simple ya existente (sin 3D), para no saturar interacciones rutinarias. Bajo
`prefers-reduced-motion`, todo colapsa a solo fundido (sin transforms) vía `MotionConfig
reducedMotion="user"`, ya configurado.

Criterio de éxito: verificación visual manual en navegador real (dev server + browser tooling) —
sticky chat realmente fijo durante el scroll de la consola, transición fluida sin saltos de layout,
reduced-motion respetado — más `pnpm run check:web`, `typecheck:web`, `build:web`, `test:web` en
verde.

## 3. Layout sticky (verificar y, si aplica, corregir)

- Verificar en navegador real que `.thread-chat-sticky` permanece fijo arriba mientras
  `.thread-execution-console` (dentro de `.thread-live-layout`, `overflow-y: auto`) se desplaza
  debajo. El mecanismo CSS leído (`overflow` no-visible ⇒ min-height automático resuelve a 0 en la
  cadena flex `.shell-chat-layout` → `.thread-live-layout`) sugiere que ya funciona, pero no se ha
  confirmado en runtime.
- Si hay corte real (p. ej. por la cadena de alturas `.ide-pane .content-frame { overflow: auto }` →
  `.shell-chat-layout { height: 100% }` → `.thread-live-layout { overflow-y: auto }` compitiendo), el
  fix es CSS puro y acotado a las clases de threads — sin tocar `.content-frame` (compartido con
  otras rutas).
- Fuera de alcance: rediseñar la separación transcript/consola — esa separación ya es "el chat arriba
  siempre visible" + "abajo los avances con scroll" tal como se pidió.

## 4. Transición "Depth dissolve" (frontend, solo visual)

### 4.1 Nuevas variantes (`motion/variants.ts`)

- `threadIntakeExit`: `opacity 1→0`, `scale 1→.94`, `y 0→-14`, `rotateX 0→8`, ~0.4s, `EASE_OUT`.
- `threadLiveEnter`: `opacity 0→1`, `scale .98→1`, `y 14→0`, `rotateX -6→0`, delay ~0.12s (se
  solapa con la salida del intake), spring suave sin rebote (`stiffness` baja, `damping` alto) o
  transición temporizada equivalente si el spring no compone bien con el stagger de los hijos.
- Reutiliza `listStagger`/`cardTransition` (ya existentes) para el transcript con `delayChildren`
  corto, y un segundo stagger con `delayChildren` mayor para la consola de ejecución, de modo que se
  lea como que carga después de que el chat ya está fijo arriba.

### 4.2 Orquestación (`ThreadConversation.tsx`)

- Envolver las ramas de retorno (intake / loading / error / live) en `AnimatePresence
  mode="popLayout" initial={false}`, keyed por una fase derivada (`'new' | 'loading' | 'error' |
  'live'`).
- Solo el par `'new' → 'live'` usa `threadIntakeExit` / `threadLiveEnter`; los demás pares (loading,
  error, cambiar de hilo ya creado) usan `panelTransition` (ya existente, sin 3D) para mantener el
  lenguaje de motion restringido del resto de la app.
- `perspective` se define en CSS, acotado a la zona de threads (`layout.css`), para que `rotateX` se
  vea con profundidad real sin afectar otras vistas.

### 4.3 Accesibilidad / reduced motion

- Ningún transform fuera del patrón ya establecido (transform + opacity) ⇒ `MotionConfig
  reducedMotion="user"` lo neutraliza automáticamente a solo fundido.
- No se toca foco/aria — la estructura semántica de `ThreadConversation` no cambia, solo el
  envoltorio de animación.

## 5. Fuera de alcance (YAGNI)

- Cualquier cambio de datos o lógica: coordinator, API, repos, contratos, websockets/polling — nada
  de esto se toca (instrucción explícita del usuario: "nada de lógica, solo visual").
- Nuevas dependencias (three.js, react-spring, etc.) — `motion` ya cubre lo pedido.
- Rediseñar la consola de ejecución o el transcript más allá de la coreografía de entrada.
- Aplicar el efecto 3D a otras rutas/páginas de la app.

## 6. Equipo y validación

- `frontend-architect`: revisa que el `AnimatePresence`/orquestación de fases no rompa el contrato
  de `ThreadConversation`/`ShellPage` ni la arquitectura existente.
- `frontend-developer-senior` / `react-senior-dev`: implementa variantes + orquestación + fix CSS
  del layout sticky si la verificación en vivo lo requiere.
- `ux-ui-director` / `accessibility-qa`: revisa timing, reduced-motion, contraste durante la
  transición, ausencia de "AI-slop" (sin gradients/glow nuevos, sin side-stripes).
- `technical-lead-senior`: aprobación final.
- Verificación: dev server real + navegador (Playwright/chrome-devtools), `pnpm run check:web`,
  `typecheck:web`, `build:web`, `test:web`. Commit quirúrgico (solo archivos de la transición de
  threads).
