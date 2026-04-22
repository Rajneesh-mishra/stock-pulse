import type { GateRow, GateInfo } from '../types';
import { Card } from './Card';
import { Badge } from './Badge';
import { PAIR_LABEL, dpFor, fmtNum } from '../lib/format';

/**
 * The underlying engine emits 7 checks. They are NOT a binary go/no-go
 * — they feed the conviction × R:R sizing call per prompts/forex_tick.md.
 *
 *   Setup signals  (#1 HTF bias, #2 At structure, #3 Confirmation)
 *     → the three checks that actually determine conviction. Thesis-level.
 *   R:R (#4)
 *     → computed at trade time, not predictive, shown only when relevant.
 *   Guardrails  (#5 Capacity, #6 Correlation, #7 Session)
 *     → platform safety; always pass in normal conditions.
 *
 * Conviction is the count of setup signals passing (0–3), and the sizing
 * recommendation maps directly from the backend verdict.
 */
const SETUP_IDS = new Set([1, 2, 3]);
const GUARD_IDS = new Set([5, 6, 7]);

function partition(gates: GateInfo[]): { setup: GateInfo[]; guards: GateInfo[] } {
  return {
    setup:  gates.filter(g => SETUP_IDS.has(g.id)).sort((a,b)=>a.id-b.id),
    guards: gates.filter(g => GUARD_IDS.has(g.id)).sort((a,b)=>a.id-b.id),
  };
}

function convictionFromSetup(setup: GateInfo[]): number {
  return setup.filter(g => g.status === 'PASS').length;
}

function sizingLabel(verdict: string): { tone: any; label: string; sub: string } {
  switch (verdict) {
    case 'FULL':
    case 'ENTER': return { tone: 'bull',    label: 'full',  sub: 'standard size' };
    case 'HALF':  return { tone: 'amber',   label: 'half',  sub: 'anticipation ok' };
    default:      return { tone: 'neutral', label: 'watch', sub: 'no entry yet' };
  }
}

function Dots({ gates, tone }: { gates: GateInfo[]; tone: 'setup' | 'guard' }) {
  return (
    <div className="inline-flex gap-1">
      {gates.map(g => {
        const pass = g.status === 'PASS';
        const soft = g.status === 'SOFT';
        const cls = pass
          ? (tone === 'setup' ? 'bg-bull' : 'bg-sky')
          : soft ? 'bg-amber/70' : 'bg-bear/30';
        return (
          <span
            key={g.id}
            title={`${g.name} · ${g.status}${g.detail ? ' — ' + g.detail : ''}`}
            className={`h-2 w-4 rounded-sm ${cls}`}
          />
        );
      })}
    </div>
  );
}

function ConvictionMeter({ score, max = 3 }: { score: number; max?: number }) {
  return (
    <div className="inline-flex items-center gap-1.5">
      <span className="num text-base font-semibold text-fg">{score}<span className="text-fg-subtle">/{max}</span></span>
      <div className="flex gap-0.5">
        {Array.from({ length: max }).map((_, i) => (
          <span key={i} className={`h-3.5 w-1.5 rounded-[1px] ${i < score ? 'bg-bull' : 'bg-ink-700'}`} />
        ))}
      </div>
    </div>
  );
}

export function GatesTable({ rows }: { rows: GateRow[] }) {
  if (!rows.length) {
    return (
      <Card className="p-8 text-center text-sm italic text-fg-muted">
        awaiting live data… this panel populates when the Python confluence engine
        has evaluated the watched pairs.
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      {/* Mobile: stacked */}
      <div className="divide-y divide-line lg:hidden">
        {rows.map(r => {
          const { setup, guards } = partition(r.gates);
          const conv = convictionFromSetup(setup);
          const sz = sizingLabel(r.verdict);
          return (
            <div key={r.epic} className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-fg">{PAIR_LABEL[r.epic]}</div>
                  <div className="mt-0.5 text-[11px] num text-fg-muted">
                    mid {fmtNum(r.mid, dpFor(r.epic))} · ATR {fmtNum(r.atr_m15, dpFor(r.epic) + 1)}
                  </div>
                </div>
                <Badge tone={sz.tone} size="sm">{sz.label}</Badge>
              </div>
              <div className="mt-3 flex items-center justify-between gap-4">
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-fg-subtle">setup</div>
                  <div className="mt-1 flex items-center gap-2">
                    <Dots gates={setup} tone="setup" />
                    <span className="text-[10px] text-fg-subtle">·</span>
                    <ConvictionMeter score={conv} />
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] uppercase tracking-wider text-fg-subtle">guardrails</div>
                  <div className="mt-1"><Dots gates={guards} tone="guard" /></div>
                </div>
              </div>
              <div className="mt-2 text-[10px] uppercase tracking-wider text-fg-subtle">{sz.sub}</div>
            </div>
          );
        })}
      </div>

      {/* Desktop: table */}
      <table className="hidden w-full text-sm lg:table">
        <thead>
          <tr className="border-b border-line bg-ink-750/40 text-left text-[10px] uppercase tracking-[0.2em] text-fg-subtle">
            <th className="px-5 py-3 font-medium">Pair</th>
            <th className="px-5 py-3 font-medium">Mid</th>
            <th className="px-5 py-3 font-medium">ATR · M15</th>
            <th className="px-5 py-3 font-medium">Setup (bias · zone · confirm)</th>
            <th className="px-5 py-3 text-center font-medium">Conviction</th>
            <th className="px-5 py-3 text-center font-medium">Guardrails</th>
            <th className="px-5 py-3 text-right font-medium">Sizing</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const { setup, guards } = partition(r.gates);
            const conv = convictionFromSetup(setup);
            const sz = sizingLabel(r.verdict);
            return (
              <tr key={r.epic} className="border-b border-line last:border-0 hover:bg-ink-750/40">
                <td className="px-5 py-3.5 font-medium text-fg">{PAIR_LABEL[r.epic]}</td>
                <td className="px-5 py-3.5 num text-fg-muted">{fmtNum(r.mid, dpFor(r.epic))}</td>
                <td className="px-5 py-3.5 num text-fg-muted">{fmtNum(r.atr_m15, dpFor(r.epic) + 1)}</td>
                <td className="px-5 py-3.5"><Dots gates={setup} tone="setup" /></td>
                <td className="px-5 py-3.5 text-center"><ConvictionMeter score={conv} /></td>
                <td className="px-5 py-3.5 text-center"><Dots gates={guards} tone="guard" /></td>
                <td className="px-5 py-3.5 text-right">
                  <div className="inline-flex flex-col items-end gap-0.5">
                    <Badge tone={sz.tone} size="sm">{sz.label}</Badge>
                    <span className="text-[10px] text-fg-subtle">{sz.sub}</span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Legend */}
      <div className="grid grid-cols-1 gap-3 border-t border-line px-5 py-4 text-[11px] text-fg-subtle sm:grid-cols-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex gap-1"><span className="h-2 w-4 rounded-sm bg-bull" /><span className="h-2 w-4 rounded-sm bg-bull" /><span className="h-2 w-4 rounded-sm bg-bear/30" /></span>
          <span>setup · HTF bias, zone, confirmation — drives conviction</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex gap-1"><span className="h-2 w-4 rounded-sm bg-sky" /><span className="h-2 w-4 rounded-sm bg-sky" /><span className="h-2 w-4 rounded-sm bg-sky" /></span>
          <span>guardrails · capacity, correlation, session</span>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone="bull" size="xs">full</Badge>
          <Badge tone="amber" size="xs">half</Badge>
          <Badge tone="neutral" size="xs">watch</Badge>
          <span>· sizing from conviction × R:R</span>
        </div>
      </div>
    </Card>
  );
}
