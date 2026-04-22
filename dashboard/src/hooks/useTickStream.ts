import { useEffect, useRef, useState } from 'react';
import type { EpicCode, LiveTick, Mode } from '../types';

export interface TickState {
  ticks: Record<EpicCode, LiveTick & { flashDir?: 'up' | 'dn' }>;
  connected: boolean;
  received: number;
}

/* Subscribes to /api/ticks/stream SSE in live mode.
   Each tick updates the ticks map and stamps a brief flashDir so the
   consuming component can play an animation one render cycle. */
export function useTickStream(mode: Mode, initial?: Record<EpicCode, LiveTick>): TickState {
  const [state, setState] = useState<TickState>({
    ticks: (initial as any) ?? ({} as any),
    connected: false,
    received: 0,
  });
  const prevMidRef = useRef<Record<string, number>>({});

  // seed from initial (once) so first render has prices
  useEffect(() => {
    if (!initial) return;
    setState(s => ({ ...s, ticks: { ...(initial as any), ...s.ticks } }));
  }, [initial]);

  useEffect(() => {
    if (mode !== 'live' || typeof EventSource === 'undefined') return;
    const es = new EventSource('/api/ticks/stream');
    es.onopen = () => setState(s => ({ ...s, connected: true }));
    es.onerror = () => setState(s => ({ ...s, connected: false }));
    es.onmessage = e => {
      try {
        const t = JSON.parse(e.data) as LiveTick;
        if (!t || !t.epic) return;
        const mid = (Number(t.bid) + Number(t.ofr)) / 2;
        const prev = prevMidRef.current[t.epic];
        const flashDir: 'up' | 'dn' | undefined =
          prev == null ? undefined : mid > prev ? 'up' : mid < prev ? 'dn' : undefined;
        prevMidRef.current[t.epic] = mid;
        setState(s => ({
          ...s,
          received: s.received + 1,
          ticks: { ...s.ticks, [t.epic]: { ...t, flashDir } as any },
        }));
      } catch { /* ignore */ }
    };
    return () => es.close();
  }, [mode]);

  return state;
}
