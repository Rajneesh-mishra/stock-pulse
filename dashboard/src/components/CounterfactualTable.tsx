import type { CounterfactualSummary } from '../types';
import { Card } from './Card';

function Cell({ rate, pips, filled }: { rate: number | null; pips: number | null; filled: number }) {
  if (rate == null) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-xs italic text-fg-subtle">pending</div>
    );
  }
  const pct = Math.round(rate * 100);
  const cls =
    pct >= 55 ? 'bg-gradient-to-br from-bull/25 to-transparent text-bull' :
    pct >= 30 ? 'bg-gradient-to-br from-amber/20 to-transparent text-amber' :
                'bg-gradient-to-br from-bear/20 to-transparent text-bear';
  const pipsStr = pips == null ? '' : (pips > 0 ? `+${pips}p` : `${pips}p`);
  return (
    <div className={`flex h-full flex-col items-center justify-center p-4 ${cls}`}>
      <div className="num text-lg font-semibold">{pct}%</div>
      <div className="mt-0.5 text-[10px] text-fg-subtle">{pipsStr} · n{filled}</div>
    </div>
  );
}

export function CounterfactualTable({ cf }: { cf: CounterfactualSummary | null }) {
  const alerts = (cf?.alerts ?? []).filter(a => a.alert_id !== 'TEST_CHAIN_VALIDATION');
  alerts.sort((x, y) => (y.fires ?? 0) - (x.fires ?? 0));

  if (!alerts.length) {
    return (
      <Card className="p-8 text-center text-sm italic text-fg-muted">No alert history yet.</Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="grid grid-cols-[1fr_70px_70px_80px] sm:grid-cols-[2fr_1fr_1fr_1fr]">
        <div className="bg-ink-750/40 p-3 text-[10px] font-medium uppercase tracking-[0.16em] text-fg-subtle">Alert</div>
        <div className="bg-ink-750/40 p-3 text-center text-[10px] font-medium uppercase tracking-[0.16em] text-fg-subtle">+1h</div>
        <div className="bg-ink-750/40 p-3 text-center text-[10px] font-medium uppercase tracking-[0.16em] text-fg-subtle">+4h</div>
        <div className="bg-ink-750/40 p-3 text-center text-[10px] font-medium uppercase tracking-[0.16em] text-fg-subtle">+24h</div>

        {alerts.map(a => {
          const rows = ['1h', '4h', '24h'] as const;
          return (
            <div key={a.alert_id} className="contents">
              <div className="border-t border-line p-3">
                <div className="truncate text-sm font-medium text-fg">{a.alert_id}</div>
                <div className="mt-0.5 text-[11px] text-fg-subtle">
                  {a.instrument} · {a.direction ?? 'no direction logged'} · {a.fires} fires
                </div>
              </div>
              {rows.map(hz => (
                <div key={hz} className="border-t border-line">
                  <Cell
                    rate={a.by_horizon?.[hz]?.hit_rate ?? null}
                    pips={a.by_horizon?.[hz]?.avg_pips ?? null}
                    filled={a.by_horizon?.[hz]?.filled ?? 0}
                  />
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
