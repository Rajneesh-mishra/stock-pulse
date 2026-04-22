import type { GateRow } from '../types';
import { Card } from './Card';
import { Badge } from './Badge';
import { PAIR_LABEL, dpFor, fmtNum } from '../lib/format';

export function GatesTable({ rows }: { rows: GateRow[] }) {
  if (!rows.length) {
    return (
      <Card className="p-8 text-center text-sm italic text-fg-muted">
        awaiting live data… this panel populates from the Python confluence engine when running against :8787.
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      {/* Mobile: stacked rows */}
      <div className="divide-y divide-line lg:hidden">
        {rows.map(r => (
          <div key={r.epic} className="p-4">
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold text-fg">{PAIR_LABEL[r.epic]}</div>
              <VerdictBadge v={r.verdict} />
            </div>
            <div className="mt-2 flex items-center justify-between text-[11px] num text-fg-muted">
              <span>mid {fmtNum(r.mid, dpFor(r.epic))}</span>
              <span>ATR {fmtNum(r.atr_m15, dpFor(r.epic) + 1)}</span>
              <span>{r.pass_count}/{r.gates.length}</span>
            </div>
            <GateDots gates={r.gates} className="mt-3" />
          </div>
        ))}
      </div>

      {/* Desktop: table */}
      <table className="hidden w-full text-sm lg:table">
        <thead>
          <tr className="border-b border-line bg-ink-750/40 text-left text-[10px] uppercase tracking-[0.2em] text-fg-subtle">
            <th className="px-5 py-3 font-medium">Pair</th>
            <th className="px-5 py-3 font-medium">Mid</th>
            <th className="px-5 py-3 font-medium">ATR · M15</th>
            <th className="px-5 py-3 text-center font-medium">Gates 1–7</th>
            <th className="px-5 py-3 text-right font-medium">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.epic} className="border-b border-line last:border-0 hover:bg-ink-750/40">
              <td className="px-5 py-3.5 font-medium text-fg">{PAIR_LABEL[r.epic]}</td>
              <td className="px-5 py-3.5 num text-fg-muted">{fmtNum(r.mid, dpFor(r.epic))}</td>
              <td className="px-5 py-3.5 num text-fg-muted">{fmtNum(r.atr_m15, dpFor(r.epic) + 1)}</td>
              <td className="px-5 py-3.5">
                <div className="flex items-center justify-center gap-3">
                  <GateDots gates={r.gates} />
                  <span className="num text-[11px] text-fg-subtle">{r.pass_count}/{r.gates.length}</span>
                </div>
              </td>
              <td className="px-5 py-3.5 text-right"><VerdictBadge v={r.verdict} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function GateDots({ gates, className = '' }: { gates: GateRow['gates']; className?: string }) {
  return (
    <div className={`inline-flex gap-1 ${className}`}>
      {gates.map(g => (
        <span
          key={g.id}
          title={`Gate ${g.id} · ${g.name} · ${g.status}${g.detail ? ' — ' + g.detail : ''}`}
          className={`h-2 w-5 rounded-sm ${
            g.status === 'PASS' ? 'bg-bull' :
            g.status === 'SOFT' ? 'bg-amber/70' :
            'bg-bear/40'
          }`}
        />
      ))}
    </div>
  );
}

function VerdictBadge({ v }: { v: string }) {
  const tone: any = v === 'FULL' || v === 'ENTER' ? 'bull' : v === 'HALF' ? 'amber' : 'neutral';
  return <Badge tone={tone} size="sm">{v}</Badge>;
}
