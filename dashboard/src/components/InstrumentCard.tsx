import { useEffect, useRef, useState } from 'react';
import type { Candle, EpicCode, LevelAlert, LiveTick } from '../types';
import { Sparkline } from './Sparkline';
import { Badge } from './Badge';
import {
  PAIR_LABEL, THEME_TAG, PIP_SIZE, dpFor, fmtNum, fmtPct, PAIR_FULL,
} from '../lib/format';

type Props = {
  epic: EpicCode;
  tick?: (LiveTick & { flashDir?: 'up' | 'dn' }) | null;
  snapshotPrice?: { bid: number; offer: number; high?: number; low?: number; change_pct?: number };
  candles?: Candle[];
  alert?: LevelAlert;
};

export function InstrumentCard({ epic, tick, snapshotPrice, candles, alert }: Props) {
  const dp = dpFor(epic);
  const pip = PIP_SIZE[epic];

  const bid = tick?.bid ?? snapshotPrice?.bid ?? alert?.current_price_ref ?? null;
  const ofr = tick?.ofr ?? snapshotPrice?.offer ?? null;
  const mid = bid != null && ofr != null ? (Number(bid) + Number(ofr)) / 2 : bid;
  const spread = bid != null && ofr != null ? (Number(ofr) - Number(bid)) / pip : null;
  const chgPct = snapshotPrice?.change_pct ?? null;
  const hi = snapshotPrice?.high ?? null;
  const lo = snapshotPrice?.low ?? null;

  // Persist flash class briefly when tick arrives
  const [flashKey, setFlashKey] = useState(0);
  const [flashDir, setFlashDir] = useState<'up' | 'dn' | null>(null);
  const lastMidRef = useRef<number | null>(null);

  useEffect(() => {
    if (mid == null) return;
    const prev = lastMidRef.current;
    lastMidRef.current = mid;
    if (prev == null) return;
    if (tick?.flashDir) {
      setFlashDir(tick.flashDir);
      setFlashKey(k => k + 1);
    }
  }, [mid, tick?.flashDir]);

  useEffect(() => {
    if (!flashDir) return;
    const t = setTimeout(() => setFlashDir(null), 750);
    return () => clearTimeout(t);
  }, [flashDir, flashKey]);

  const flashClass = flashDir === 'up'
    ? 'animate-flash-up'
    : flashDir === 'dn'
    ? 'animate-flash-dn'
    : '';

  const closes = (candles ?? []).map(c => Number(c.close)).filter(Number.isFinite);

  const changeTone: 'bull' | 'bear' | null =
    chgPct == null ? null : chgPct > 0.02 ? 'bull' : chgPct < -0.02 ? 'bear' : null;

  return (
    <div className={
      `group flex flex-col gap-4 rounded-xl2 border bg-ink-800 p-4 sm:p-5
       transition-all duration-200 hover:border-line-strong hover:-translate-y-[1px]
       ${alert ? 'border-bull/20' : 'border-line'}`
    }>
      {/* Head */}
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <div className="text-sm font-semibold tracking-tight text-fg">{PAIR_LABEL[epic]}</div>
          <div className="mt-0.5 text-[10px] uppercase tracking-[0.16em] text-fg-subtle">{THEME_TAG[epic]}</div>
        </div>
        {alert && (
          <Badge tone={alert.direction === 'buy' ? 'bull' : 'bear'} size="xs">
            {alert.direction} · {fmtNum(alert.level, dp)}
          </Badge>
        )}
      </div>

      {/* Price */}
      <div className="flex items-baseline justify-between gap-3">
        <div
          key={flashKey}
          className={`num rounded-md px-1 -mx-1 text-2xl font-medium tracking-tight text-fg sm:text-[26px] ${flashClass}`}
          title={PAIR_FULL[epic]}
        >
          {mid != null ? fmtNum(mid, dp) : '—'}
        </div>
        {chgPct != null && (
          <div className={`num text-sm font-medium ${changeTone === 'bull' ? 'text-bull' : changeTone === 'bear' ? 'text-bear' : 'text-fg-muted'}`}>
            {fmtPct(chgPct)}
          </div>
        )}
      </div>

      {/* Sparkline */}
      <div className="relative">
        <Sparkline
          values={closes}
          width={240}
          height={52}
          dir={changeTone === 'bull' ? 'up' : changeTone === 'bear' ? 'dn' : undefined}
        />
      </div>

      {/* Meta row */}
      <dl className="grid grid-cols-3 gap-2 text-[11px] num text-fg-muted">
        <div>
          <dt className="text-[9px] uppercase tracking-[0.16em] text-fg-subtle">Bid</dt>
          <dd className="mt-0.5">{bid != null ? fmtNum(bid, dp) : '—'}</dd>
        </div>
        <div>
          <dt className="text-[9px] uppercase tracking-[0.16em] text-fg-subtle">Ofr</dt>
          <dd className="mt-0.5">{ofr != null ? fmtNum(ofr, dp) : '—'}</dd>
        </div>
        <div>
          <dt className="text-[9px] uppercase tracking-[0.16em] text-fg-subtle">Spread</dt>
          <dd className="mt-0.5">{spread != null ? `${spread.toFixed(1)}p` : '—'}</dd>
        </div>
        <div>
          <dt className="text-[9px] uppercase tracking-[0.16em] text-fg-subtle">Day hi</dt>
          <dd className="mt-0.5">{hi != null ? fmtNum(hi, dp) : '—'}</dd>
        </div>
        <div>
          <dt className="text-[9px] uppercase tracking-[0.16em] text-fg-subtle">Day lo</dt>
          <dd className="mt-0.5">{lo != null ? fmtNum(lo, dp) : '—'}</dd>
        </div>
        <div>
          <dt className="text-[9px] uppercase tracking-[0.16em] text-fg-subtle">Candles</dt>
          <dd className="mt-0.5">{(candles ?? []).length || '—'}</dd>
        </div>
      </dl>
    </div>
  );
}
