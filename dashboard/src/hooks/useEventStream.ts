import { useEffect, useRef, useState } from 'react';
import type { ForexEvent, Mode } from '../types';
import { getRecentEvents, getStaticFeed } from '../lib/api';

export function useEventStream(mode: Mode, max = 60) {
  const [events, setEvents] = useState<ForexEvent[]>([]);
  const seenRef = useRef<Set<string>>(new Set());

  const prepend = (ev: ForexEvent) => {
    const id = ev.event_id ?? (ev.ts_utc ? `ts_${ev.ts_utc}` : `r_${Math.random()}`);
    if (seenRef.current.has(id)) return;
    seenRef.current.add(id);
    setEvents(curr => [{ ...ev, event_id: id }, ...curr].slice(0, max));
  };

  // Backfill + subscribe
  useEffect(() => {
    if (mode === 'connecting') return;
    let alive = true;

    (async () => {
      if (mode === 'live') {
        const d = await getRecentEvents(30);
        if (!alive) return;
        // Oldest → newest so newest ends up on top after prepends.
        (d?.events ?? []).forEach(prepend);
      } else {
        const d = await getStaticFeed();
        if (!alive) return;
        (d?.ticks ?? []).forEach((t: any) =>
          prepend({
            event_id: 't_' + (t.ts_utc || t.t),
            type: t.trigger || 'tick',
            ts_utc: t.ts_utc || t.t,
            payload: { note: t.summary || t.note },
          }),
        );
      }
    })();

    if (mode === 'live' && typeof EventSource !== 'undefined') {
      const es = new EventSource('/api/events/stream');
      es.onmessage = e => {
        try { prepend(JSON.parse(e.data) as ForexEvent); } catch { /* ignore */ }
      };
      return () => { alive = false; es.close(); };
    }
    return () => { alive = false; };
  }, [mode]);

  return events;
}
