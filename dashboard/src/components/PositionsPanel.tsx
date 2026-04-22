import type { Trade } from '../types';
import { Card } from './Card';
import { Badge } from './Badge';
import { PAIR_LABEL, dpFor, fmtMoney, fmtNum } from '../lib/format';

type Props = {
  positions: any[];
  trades: Trade[];
};

export function PositionsPanel({ positions, trades }: Props) {
  return (
    <div className="space-y-6">
      {/* Open */}
      <Card>
        <div className="border-b border-line px-5 py-3 text-[11px] font-medium uppercase tracking-[0.16em] text-fg-subtle">
          Open positions
        </div>
        {positions.length ? (
          <div className="divide-y divide-line">
            {positions.map((p, i) => {
              const pos = p.position ?? p;
              const epic = p.market?.epic ?? pos.instrument;
              const dir = (pos.direction ?? '').toString().toLowerCase();
              const upl = Number(pos.upl ?? pos.unrealisedPL ?? 0);
              const dp = dpFor(epic);
              return (
                <div key={i} className="grid grid-cols-[auto_1fr_auto] items-center gap-4 px-5 py-4">
                  <Badge tone={dir === 'buy' ? 'bull' : 'bear'} size="xs">{dir}</Badge>
                  <div>
                    <div className="font-medium text-fg">{PAIR_LABEL[epic as keyof typeof PAIR_LABEL] ?? epic}</div>
                    <div className="mt-0.5 text-[11px] num text-fg-muted">
                      entry {fmtNum(pos.level, dp)} · SL {fmtNum(pos.stopLevel, dp)} · TP {fmtNum(pos.profitLevel, dp)}
                    </div>
                  </div>
                  <div className={`num text-right text-lg font-semibold ${upl > 0 ? 'text-bull' : upl < 0 ? 'text-bear' : 'text-fg'}`}>
                    {fmtMoney(upl)}
                    <div className="mt-0.5 text-[9px] uppercase tracking-wider text-fg-subtle">unrealised</div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 px-5 py-10 text-center">
            <div className="text-[22px] font-light text-fg-faint">∅</div>
            <div className="text-sm text-fg-muted">Capital is preserved — no positions at risk right now.</div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-fg-subtle">alerts armed · waiting for conviction</div>
          </div>
        )}
      </Card>

      {/* Closed trades */}
      <Card>
        <div className="border-b border-line px-5 py-3 text-[11px] font-medium uppercase tracking-[0.16em] text-fg-subtle">
          Closed trades · lifetime
        </div>
        {trades.length ? (
          <div className="divide-y divide-line">
            {[...trades].reverse().map((t, i) => {
              const dp = dpFor(t.instrument);
              const dir = (t.direction ?? '').toLowerCase();
              const pnl = Number(t.pnl ?? 0);
              return (
                <div key={i} className="grid grid-cols-[auto_1fr_auto] items-start gap-4 px-5 py-4">
                  <Badge tone={dir === 'buy' ? 'bull' : 'bear'} size="xs">{dir}</Badge>
                  <div>
                    <div className="font-medium text-fg">{PAIR_LABEL[t.instrument] ?? t.instrument}</div>
                    <div className="mt-0.5 text-[11px] num text-fg-muted">
                      {fmtNum(t.entry_price, dp)} → {fmtNum(t.exit_price, dp)} · {t.result}
                    </div>
                    {t.lessons && (
                      <div className="mt-2 max-w-[52ch] text-[12px] italic text-fg-muted">"{t.lessons}"</div>
                    )}
                  </div>
                  <div className={`num text-right text-lg font-semibold ${pnl > 0.5 ? 'text-bull' : pnl < -0.5 ? 'text-bear' : 'text-fg'}`}>
                    {fmtMoney(pnl)}
                    <div className="mt-0.5 text-[9px] uppercase tracking-wider text-fg-subtle">realised</div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="px-5 py-10 text-center text-sm italic text-fg-muted">No trades yet.</div>
        )}
      </Card>
    </div>
  );
}
