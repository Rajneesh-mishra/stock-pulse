import { useEffect, useState } from 'react';
import type { Candle, EpicCode, Mode } from '../types';
import { getCandles } from '../lib/api';
import { INSTRUMENTS } from '../lib/format';

/* Fetch 60 × M15 candles per instrument in parallel on mount, then every
   2 minutes. Static mode gets nothing (no candle API on GH Pages). */
export function useCandles(mode: Mode): Record<EpicCode, Candle[]> {
  const [map, setMap] = useState<Record<EpicCode, Candle[]>>({} as any);

  useEffect(() => {
    if (mode !== 'live') return;
    let alive = true;

    const refresh = async () => {
      const results = await Promise.all(
        INSTRUMENTS.map(async epic => {
          const d = await getCandles(epic, 'MINUTE_15', 60);
          return [epic, d?.candles ?? []] as const;
        }),
      );
      if (!alive) return;
      const next: Record<EpicCode, Candle[]> = {} as any;
      for (const [epic, c] of results) (next as any)[epic] = c;
      setMap(next);
    };

    refresh();
    const id = setInterval(refresh, 120_000);
    return () => { alive = false; clearInterval(id); };
  }, [mode]);

  return map;
}
