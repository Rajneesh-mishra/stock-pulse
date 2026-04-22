import type { ReactNode } from 'react';

type Tone = 'default' | 'bull' | 'bear' | 'amber' | 'muted';

export function Metric({
  label, value, sub, tone = 'default', icon,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  icon?: ReactNode;
}) {
  const toneClass =
    tone === 'bull'  ? 'text-bull'  :
    tone === 'bear'  ? 'text-bear'  :
    tone === 'amber' ? 'text-amber' :
    tone === 'muted' ? 'text-fg-muted italic' :
    'text-fg';

  return (
    <div className="flex flex-col gap-1.5 p-5 sm:p-6">
      <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-fg-subtle">
        {icon}
        <span>{label}</span>
      </div>
      <div className={`num text-2xl font-medium tracking-tight sm:text-[30px] ${toneClass}`}>
        {value}
      </div>
      {sub && <div className="text-xs text-fg-muted num">{sub}</div>}
    </div>
  );
}
