import type { Mode } from '../types';

export function StatusPill({ mode, ticks }: { mode: Mode; ticks?: number }) {
  const live = mode === 'live';
  const color = live ? 'bg-bull' : mode === 'static' ? 'bg-amber' : 'bg-fg-faint';
  const label = live ? 'Live · WS' : mode === 'static' ? 'Static' : 'Connecting…';
  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-line bg-ink-750/60 px-2.5 py-1 text-[11px] font-medium backdrop-blur-md">
      <span className={`h-1.5 w-1.5 rounded-full ${color} ${live ? 'animate-pulse-dot' : ''}`} />
      <span className="text-fg">{label}</span>
      {ticks != null && ticks > 0 && (
        <span className="hidden sm:inline text-fg-subtle num">· {ticks.toLocaleString()} ticks</span>
      )}
    </div>
  );
}
