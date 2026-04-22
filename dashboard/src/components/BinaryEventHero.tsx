import { useEffect, useState } from 'react';
import type { BinaryEvent } from '../types';
import { Badge } from './Badge';

type Posture = 'none' | 'passed' | 'blackout' | 'caution' | 'armed' | 'quiet';

function computePosture(b?: BinaryEvent): { posture: Posture; label: string; headline: string } {
  if (!b || !b.active || !b.deadline_utc) {
    return {
      posture: 'quiet',
      label: 'Tape is quiet',
      headline: 'No active binary event. Watch the levels — opportunity comes to those who wait, sized.',
    };
  }
  const dt = new Date(b.deadline_utc).getTime() - Date.now();
  if (dt < -60_000) return { posture: 'passed',   label: 'Deadline passed · reassess', headline: 'The deadline is behind us. What the market does next is the thesis, not the rhetoric.' };
  if (dt < 30*60_000) return { posture: 'blackout', label: 'T-30 · blackout',           headline: 'Thirty minutes. No new entries. Manage what is already open.' };
  if (dt < 24*3600_000) return { posture: 'caution', label: 'Elevated caution',          headline: 'Elevated caution. Conviction 4+ only. Half size until the binary resolves.' };
  return { posture: 'armed', label: 'Armed · deadline on horizon', headline: 'The tape is armed. Watch the levels, not the rhetoric.' };
}

function pad(n: number): string { return n.toString().padStart(2, '0'); }

function Counter({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex min-w-[64px] flex-col items-start">
      <span className="num text-4xl font-medium leading-none text-fg sm:text-5xl lg:text-6xl">{value}</span>
      <span className="mt-1.5 text-[10px] font-medium uppercase tracking-[0.22em] text-fg-subtle">{label}</span>
    </div>
  );
}

export function BinaryEventHero({ event }: { event?: BinaryEvent }) {
  const { posture, label, headline } = computePosture(event);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const hasDeadline = event?.active && event.deadline_utc;
  const diff = hasDeadline ? new Date(event!.deadline_utc!).getTime() - now : 0;
  const past = diff < 0;
  const abs = Math.abs(diff);
  const d = Math.floor(abs / 86400000);
  const h = Math.floor(abs / 3600000) % 24;
  const m = Math.floor(abs / 60000) % 60;
  const s = Math.floor(abs / 1000) % 60;
  const dLabel = past ? 'Past' : 'Days';

  const toneClass = {
    quiet:    'from-ink-800 to-ink-800',
    armed:    'from-emerald-500/5 to-sky-500/5',
    caution:  'from-amber/10 to-amber/0',
    blackout: 'from-bear/10 to-bear/0',
    passed:   'from-ink-800 to-ink-800',
    none:     'from-ink-800 to-ink-800',
  }[posture];

  const ringClass = {
    quiet:    'border-line',
    armed:    'border-bull/30 shadow-glow',
    caution:  'border-amber/40',
    blackout: 'border-bear/40',
    passed:   'border-line',
    none:     'border-line',
  }[posture];

  const postureBadge: Record<Posture, { tone: any; text: string }> = {
    quiet:    { tone: 'neutral', text: 'quiet' },
    armed:    { tone: 'bull',    text: 'armed' },
    caution:  { tone: 'amber',   text: 'caution' },
    blackout: { tone: 'bear',    text: 'blackout' },
    passed:   { tone: 'violet',  text: 'passed' },
    none:     { tone: 'neutral', text: 'none' },
  };

  return (
    <div className={`relative overflow-hidden rounded-xl2 border bg-gradient-to-br ${toneClass} ${ringClass}`}>
      <div className="absolute inset-0 grid-bg" aria-hidden="true" />
      <div className="relative p-6 sm:p-8 lg:p-10">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-[0.24em] text-fg-subtle">
          <Badge tone={postureBadge[posture].tone} size="xs">{postureBadge[posture].text}</Badge>
          {event?.name && <span className="text-fg-muted">{event.name.replaceAll('_', ' ')}</span>}
          <span>·</span>
          <span>{label}</span>
        </div>

        <h1 className="max-w-[36ch] text-2xl font-semibold leading-[1.15] tracking-tight text-fg sm:text-3xl lg:text-4xl">
          {headline}
        </h1>

        {hasDeadline ? (
          <div className="mt-7 flex flex-wrap items-end gap-4 sm:gap-6">
            <Counter value={pad(d)} label={dLabel} />
            <span className="num text-3xl font-light leading-none text-fg-faint sm:text-4xl lg:text-5xl">:</span>
            <Counter value={pad(h)} label="Hours" />
            <span className="num text-3xl font-light leading-none text-fg-faint sm:text-4xl lg:text-5xl">:</span>
            <Counter value={pad(m)} label="Min" />
            <span className="num text-3xl font-light leading-none text-fg-faint sm:text-4xl lg:text-5xl">:</span>
            <Counter value={pad(s)} label="Sec" />
          </div>
        ) : (
          <div className="mt-7 inline-flex items-center rounded-md bg-ink-750 px-3 py-2 text-[11px] uppercase tracking-[0.18em] text-fg-subtle">
            no deadline · operating on standard cadence
          </div>
        )}

        <div className="mt-8 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line pt-5 text-xs sm:grid-cols-4">
          <Meta k="Deadline" v={event?.deadline_utc ? new Date(event.deadline_utc).toUTCString().replace('GMT', 'UTC') : '—'} />
          <Meta k="Verified" v={event?.verified ? <span className="text-bull">confirmed</span> : <span className="text-bear">unverified</span>} />
          <Meta k="Sources" v={event?.sources?.length ? `${event.sources.length} cited` : 'none yet'} />
          <Meta k="Event"   v={event?.name ? event.name.replaceAll('_', ' ') : '—'} />
        </div>
      </div>
    </div>
  );
}

function Meta({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">{k}</div>
      <div className="mt-1 truncate text-[13px] text-fg">{v}</div>
    </div>
  );
}
