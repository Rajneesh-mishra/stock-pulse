import type { CounterfactualSummary, EpicCode, LevelAlert, LiveTick } from '../types';
import { Card } from './Card';
import { Badge } from './Badge';
import { PAIR_LABEL, PIP_SIZE, dpFor, fmtNum, relativeTime, truncate } from '../lib/format';

function Gauge({ pct }: { pct: number }) {
  return (
    <div className="relative mt-3 h-1 w-full rounded-full bg-ink-700">
      <div
        className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-amber to-amber/30"
        style={{ width: `${pct}%` }}
      />
      <div
        className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink-900 bg-fg ring-2 ring-amber"
        style={{ left: `${pct}%` }}
      />
    </div>
  );
}

function HitChip({ h, rate, pips, filled }: { h: string; rate: number | null; pips: number | null; filled: number }) {
  if (rate == null) {
    return (
      <div className="flex flex-col items-center rounded-md border border-line px-2.5 py-1.5 opacity-50">
        <div className="num text-xs text-fg-muted">—</div>
        <div className="text-[9px] uppercase tracking-wider text-fg-subtle">{h}</div>
      </div>
    );
  }
  const pct = Math.round(rate * 100);
  const tone = pct >= 55 ? 'border-bull/50 text-bull' : pct >= 30 ? 'border-line-strong text-fg' : 'border-bear/50 text-bear';
  return (
    <div className={`flex flex-col items-center rounded-md border ${tone} px-2.5 py-1.5`}>
      <div className="num text-xs font-semibold">{pct}%</div>
      <div className="text-[9px] uppercase tracking-wider text-fg-subtle">{h} · n{filled}</div>
    </div>
  );
}

type Props = {
  alerts: LevelAlert[];
  counterfactual: CounterfactualSummary | null;
  ticks: Record<EpicCode, LiveTick>;
};

export function AlertList({ alerts, counterfactual, ticks }: Props) {
  const cfMap: Record<string, any> = {};
  (counterfactual?.alerts ?? []).forEach(a => { cfMap[a.alert_id] = a; });

  if (!alerts.length) {
    return (
      <Card className="flex items-center justify-center p-10 text-center">
        <div className="max-w-sm">
          <div className="text-lg font-medium text-fg">No armed triggers</div>
          <div className="mt-1 text-sm text-fg-muted">The watchlist is clean. New setups will appear here as the thesis evolves.</div>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {alerts.map(a => {
        const dp = dpFor(a.instrument);
        const pip = PIP_SIZE[a.instrument];
        const live = ticks[a.instrument];
        const cur = live ? (live.bid + live.ofr) / 2 : a.current_price_ref;
        const distPips = cur != null ? Math.abs(Number(cur) - Number(a.level)) / pip : null;
        const pct = distPips != null ? Math.max(0, Math.min(100, 100 - (distPips / 200) * 100)) : 0;
        const stats = cfMap[a.id];
        const h1 = stats?.by_horizon?.['1h'];
        const h4 = stats?.by_horizon?.['4h'];
        const h24 = stats?.by_horizon?.['24h'];

        return (
          <Card key={a.id} interactive className="p-5 sm:p-6">
            <div className="grid gap-5 lg:grid-cols-[260px_1fr_220px]">
              {/* Left: pair + level + proximity */}
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={a.direction === 'buy' ? 'bull' : 'bear'} size="sm">{a.direction}</Badge>
                  <span className="text-[10px] uppercase tracking-wider text-fg-subtle">{a.id}</span>
                </div>
                <div className="mt-3 text-[28px] font-semibold tracking-tight text-fg sm:text-[32px]">
                  {PAIR_LABEL[a.instrument]}
                </div>
                <div className="mt-1 text-sm text-fg-muted">
                  trigger <span className="num font-semibold text-amber">{fmtNum(a.level, dp)}</span>
                  <span className="mx-1.5 text-fg-subtle">·</span>
                  live <span className="num">{fmtNum(cur, dp)}</span>
                </div>
                <div className="mt-4 text-[10px] uppercase tracking-[0.14em] text-fg-subtle">
                  proximity · <span className="text-fg font-semibold num">{distPips != null ? `${distPips.toFixed(1)}p` : '—'}</span> from trigger
                </div>
                <Gauge pct={pct} />
                <div className="mt-1.5 flex justify-between text-[10px] uppercase tracking-wider text-fg-subtle">
                  <span>trigger</span><span>far · 200p</span>
                </div>
              </div>

              {/* Middle: thesis */}
              <div className="min-w-0">
                <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">thesis</div>
                <p className="mt-2 text-[13.5px] leading-relaxed text-fg-muted">
                  {truncate(a.note, 400)}
                </p>
              </div>

              {/* Right: stats */}
              <div className="flex flex-col gap-3 lg:border-l lg:border-line lg:pl-6">
                <StatRow k="updated" v={relativeTime(a.last_updated)} />
                <StatRow k="cooldown" v={`${Math.round((a.cooldown_sec ?? 0) / 60)}m`} />
                <StatRow k="fires" v={stats?.fires ?? 0} />
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-fg-subtle">hit rate · horizon</div>
                  <div className="mt-2 grid grid-cols-3 gap-1.5">
                    <HitChip h="1h"  rate={h1?.hit_rate ?? null}  pips={h1?.avg_pips ?? null}  filled={h1?.filled ?? 0} />
                    <HitChip h="4h"  rate={h4?.hit_rate ?? null}  pips={h4?.avg_pips ?? null}  filled={h4?.filled ?? 0} />
                    <HitChip h="24h" rate={h24?.hit_rate ?? null} pips={h24?.avg_pips ?? null} filled={h24?.filled ?? 0} />
                  </div>
                </div>
              </div>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between text-sm">
      <span className="text-[10px] uppercase tracking-wider text-fg-subtle">{k}</span>
      <span className="num font-medium text-fg">{v}</span>
    </div>
  );
}
