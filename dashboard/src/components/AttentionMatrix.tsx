import { useEffect, useState } from 'react';
import { Card } from './Card';
import { Badge } from './Badge';
import { PAIR_LABEL, INSTRUMENTS } from '../lib/format';
import type { EpicCode, Watchlist, Mode } from '../types';

type SwingState = 'ARMED' | 'WATCH' | 'NONE';
type ScalpState  = 'ACTIVE' | 'OFF' | 'HALTED' | 'NA';

interface Row {
  epic: EpicCode;
  swing: SwingState;
  scalp: ScalpState;
  reason: string;
}

/**
 * Derive the attention matrix client-side from the published state + watchlist
 * + scalp config. This gives the matrix even before Claude writes one to
 * regime_note. Once Claude does write one, we could swap to reading it.
 */
function deriveRows(watchlist: Watchlist | null, scalpConfig: any, haltedPairs: string[]): Row[] {
  const alerts = (watchlist?.level_alerts ?? watchlist?.alerts ?? []) as any[];
  const alertsByInst: Record<string, any[]> = {};
  for (const a of alerts) {
    (alertsByInst[a.instrument] ??= []).push(a);
  }
  const scalpPairs = scalpConfig?.pairs ?? {};

  return INSTRUMENTS.map<Row>(epic => {
    const pairAlerts = alertsByInst[epic] ?? [];
    const sc = scalpPairs[epic];

    let swing: SwingState = 'NONE';
    let reason = '';
    if (pairAlerts.length > 0) {
      swing = 'WATCH';
      const note = (pairAlerts[0].note ?? '').toString();
      reason = `${pairAlerts.length} alert${pairAlerts.length > 1 ? 's' : ''} · ${
        pairAlerts[0].direction ?? ''} ${pairAlerts[0].level} · ${note.slice(0, 60)}${note.length > 60 ? '…' : ''}`;
    } else {
      reason = 'no active swing alert';
    }

    let scalp: ScalpState = 'NA';
    if (sc) {
      if (sc.enabled === false) {
        scalp = sc.reason ? 'NA' : 'OFF';
      } else if (sc.enabled === true) {
        scalp = 'ACTIVE';
      }
      if (haltedPairs.includes(epic)) scalp = 'HALTED';
    }

    if (swing === 'NONE' && scalp === 'ACTIVE') {
      reason = `scalp mode: ${sc?.mode ?? 'range_extreme'} · ${(sc?.sessions ?? []).join(', ')}`;
    } else if (swing === 'NONE' && sc?.reason) {
      reason = sc.reason;
    }

    return { epic, swing, scalp, reason };
  });
}

function SwingBadge({ s }: { s: SwingState }) {
  const tone = s === 'ARMED' ? 'bull' : s === 'WATCH' ? 'amber' : 'neutral';
  return <Badge tone={tone} size="xs">{s}</Badge>;
}

function ScalpBadge({ s }: { s: ScalpState }) {
  if (s === 'ACTIVE') return <Badge tone="bull" size="xs">ACTIVE</Badge>;
  if (s === 'HALTED') return <Badge tone="bear" size="xs">HALTED</Badge>;
  if (s === 'OFF') return <Badge tone="neutral" size="xs">OFF</Badge>;
  return <span className="text-[10px] uppercase tracking-wider text-fg-faint">n/a</span>;
}

export function AttentionMatrix({ mode, watchlist }: { mode: Mode; watchlist: Watchlist | null }) {
  const [scalpConfig, setScalpConfig] = useState<any>(null);
  const [scalpStatus, setScalpStatus] = useState<any>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [cfg, st] = await Promise.all([
          fetch('/data/forex/scalp_config.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
          fetch('/data/forex/scalp_status.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
        ]);
        if (!alive) return;
        if (cfg) setScalpConfig(cfg);
        if (st) setScalpStatus(st);
      } catch { /* silent */ }
    };
    load();
    const id = setInterval(load, mode === 'live' ? 5000 : 30000);
    return () => { alive = false; clearInterval(id); };
  }, [mode]);

  const haltedPairs = Object.keys(scalpStatus?.halted ?? {});
  const rows = deriveRows(watchlist, scalpConfig, haltedPairs);

  const coverageCount = rows.filter(r => r.swing !== 'NONE' || r.scalp === 'ACTIVE').length;

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-line bg-ink-750/40 px-5 py-3">
        <div className="text-[10px] uppercase tracking-[0.16em] text-fg-subtle">
          coverage · {coverageCount}/9 pairs under active attention
        </div>
        <div className="text-[10px] uppercase tracking-wider text-fg-faint">
          updates every {mode === 'live' ? '5s' : '30s'}
        </div>
      </div>
      <div className="divide-y divide-line">
        {rows.map(r => (
          <div key={r.epic} className="grid grid-cols-[110px_auto_auto_1fr] items-center gap-3 px-5 py-3 sm:grid-cols-[130px_90px_90px_1fr]">
            <div className="text-sm font-semibold text-fg">{PAIR_LABEL[r.epic]}</div>
            <SwingBadge s={r.swing} />
            <ScalpBadge s={r.scalp} />
            <div className="truncate text-[12px] text-fg-muted">{r.reason}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}
