import type {
  Snapshot, Watchlist, CounterfactualSummary, Candle, GateRow,
  ForexEvent, EpicCode, Mode,
} from '../types';

/* fetch JSON with cache busting; return null on any failure */
export async function loadJSON<T>(url: string, init?: RequestInit): Promise<T | null> {
  try {
    const u = url.includes('?') ? `${url}&_=${Date.now()}` : `${url}?_=${Date.now()}`;
    const r = await fetch(u, { cache: 'no-store', ...init });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

/* Live mode probes /api/snapshot on init. If it responds, we're running
   against the local Python server (http://127.0.0.1:8787). If not,
   we're deployed on GitHub Pages — read the published JSON snapshots. */
export async function detectMode(): Promise<Mode> {
  const r = await loadJSON<Snapshot>('/api/snapshot');
  return r && r.ts ? 'live' : 'static';
}

export async function getSnapshot(): Promise<Snapshot | null> {
  return loadJSON<Snapshot>('/api/snapshot');
}

export async function getGatesAll(): Promise<{ instruments: GateRow[] } | null> {
  return loadJSON<{ instruments: GateRow[] }>('/api/gates/all');
}

export async function getCandles(epic: EpicCode, resolution = 'MINUTE_15', count = 60):
  Promise<{ candles: Candle[] } | null> {
  return loadJSON<{ candles: Candle[] }>(
    `/api/candles?epic=${encodeURIComponent(epic)}&resolution=${resolution}&count=${count}`,
  );
}

export async function getRecentEvents(n = 30):
  Promise<{ events: ForexEvent[] } | null> {
  return loadJSON<{ events: ForexEvent[] }>(`/api/events?n=${n}`);
}

export async function getStaticWatchlist(): Promise<Watchlist | null> {
  return loadJSON<Watchlist>('/data/forex/watchlist.json');
}
export async function getStaticState(): Promise<Snapshot['state_file'] | null> {
  return loadJSON<Snapshot['state_file']>('/data/forex/state.json');
}
export async function getCounterfactual(): Promise<CounterfactualSummary | null> {
  return loadJSON<CounterfactualSummary>('/data/forex/counterfactual.json');
}
export async function getStaticFeed(): Promise<{ ticks: any[] } | null> {
  return loadJSON<{ ticks: any[] }>('/data/forex/feed.json');
}
