import type { ReactNode } from 'react';

type Tone = 'bull' | 'bear' | 'amber' | 'sky' | 'violet' | 'neutral';

export function Badge({
  children,
  tone = 'neutral',
  size = 'sm',
  className = '',
}: {
  children: ReactNode;
  tone?: Tone;
  size?: 'xs' | 'sm' | 'md';
  className?: string;
}) {
  const tones: Record<Tone, string> = {
    bull:    'bg-bull-dim text-bull border-bull/20',
    bear:    'bg-bear-dim text-bear border-bear/20',
    amber:   'bg-amber-dim text-amber border-amber/20',
    sky:     'bg-sky-dim text-sky border-sky/20',
    violet:  'bg-violet-dim text-violet border-violet/20',
    neutral: 'bg-ink-700 text-fg-muted border-line',
  };
  const sizes = {
    xs: 'text-[10px] px-1.5 py-0.5 tracking-wider',
    sm: 'text-[11px] px-2 py-0.5 tracking-wider',
    md: 'text-xs px-2.5 py-1 tracking-wider',
  };
  return (
    <span
      className={
        'inline-flex items-center gap-1 rounded-md border font-medium uppercase ' +
        tones[tone] + ' ' + sizes[size] + ' ' + className
      }
    >
      {children}
    </span>
  );
}
