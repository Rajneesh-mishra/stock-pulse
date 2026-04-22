import { useEffect, useState, useRef } from 'react';
import type { Mode, Snapshot } from '../types';
import {
  getSnapshot, getGatesAll, getCounterfactual,
  getStaticState, getStaticWatchlist,
} from '../lib/api';
import type { CounterfactualSummary, GateRow, Watchlist } from '../types';

export interface SnapshotBundle {
  snapshot: Snapshot | null;
  watchlist: Watchlist | null;
  gates: GateRow[];
  counterfactual: CounterfactualSummary | null;
  loading: boolean;
  lastUpdated: number;
}

/* Live mode: polls /api/snapshot + /api/gates/all every 5s.
   Static mode: fetches /data/forex/*.json every 30s. */
export function useSnapshot(mode: Mode): SnapshotBundle {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [watchlist, setWatchlist] = useState<Watchlist | null>(null);
  const [gates, setGates] = useState<GateRow[]>([]);
  const [cf, setCF] = useState<CounterfactualSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [ts, setTs] = useState(0);

  const started = useRef(false);

  useEffect(() => {
    if (mode === 'connecting' || started.current) return;
    started.current = true;

    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (mode === 'live') {
        const [snap, gAll, c] = await Promise.all([
          getSnapshot(),
          getGatesAll(),
          cf ? Promise.resolve(cf) : getCounterfactual(),
        ]);
        if (!alive) return;
        if (snap) {
          setSnapshot(snap);
          // Normalize watchlist: live API uses `alerts`, static uses `level_alerts`
          const wl = snap.watchlist;
          if (wl) {
            setWatchlist({
              level_alerts: wl.alerts ?? wl.level_alerts ?? [],
              structure_watch: wl.structure_watch ?? [],
              instruments: wl.instruments,
            });
          }
        }
        if (gAll?.instruments) setGates(gAll.instruments);
        if (c) setCF(c);
        setLoading(false);
        setTs(Date.now());
        timer = setTimeout(tick, 5_000);
      } else {
        const [sf, wl, c] = await Promise.all([
          getStaticState(),
          getStaticWatchlist(),
          getCounterfactual(),
        ]);
        if (!alive) return;
        setSnapshot({ state_file: sf ?? undefined });
        setWatchlist(wl ?? null);
        setCF(c ?? null);
        setGates([]);
        setLoading(false);
        setTs(Date.now());
        timer = setTimeout(tick, 30_000);
      }
    };

    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [mode]);

  return { snapshot, watchlist, gates, counterfactual: cf, loading, lastUpdated: ts };
}
