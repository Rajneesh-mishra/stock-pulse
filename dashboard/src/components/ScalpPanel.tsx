import { useEffect, useState } from 'react';
import { Card } from './Card';
import { Badge } from './Badge';
import { PAIR_LABEL, dpFor, fmtNum, dateTimeIST } from '../lib/format';
import type { EpicCode, Mode } from '../types';

interface ScalpConfig {
  global?: {
    enabled?: boolean;
    shadow_mode?: boolean;
    daily_loss_cap_usd?: number;
    max_concurrent?: number;
    risk_pct_per_scalp?: number;
    max_hold_minutes?: number;
  };
  pairs?: Record<string, {
    enabled?: boolean;
    mode?: string;
    bias?: string;
    sessions?: string[];
    reason?: string;
  }>;
}

interface ScalpStatus {
  pid?: number;
  last_step_utc?: string;
  heartbeat?: number;
  reason?: string;
  shadow?: boolean;
  daily_pnl_usd?: number;
  open?: Record<string, any>;
  halted?: Record<string, string>;
  actions?: Array<{ epic: string; status: string; how?: string; pnl?: number; detail?: string }>;
}

interface ScalpTrade {
  opened_at?: string;
  closed_at?: string;
  epic: EpicCode;
  direction?: 'BUY' | 'SELL' | string;
  setup?: string;
  entry?: number;
  exit?: number;
  sl?: number;
  tp?: number;
  size?: number;
  how?: string;
  pnl_usd?: number;
  held_min?: number;
  shadow?: boolean;
}

interface ScalpLedger {
  generated_at?: string;
  stats?: {
    all?: Stats;
    today?: Stats;
    rejected_total?: number;
  };
  trades?: ScalpTrade[];
}

interface Stats {
  count: number;
  wins: number;
  losses: number;
  time_exits: number;
  win_rate: number | null;
  net_pips: number;
  net_pnl_usd: number;
}

export function ScalpPanel({ mode }: { mode: Mode }) {
  const [config, setConfig] = useState<ScalpConfig | null>(null);
  const [status, setStatus] = useState<ScalpStatus | null>(null);
  const [ledger, setLedger] = useState<ScalpLedger | null>(null);

  useEffect(() => {
    let alive = true;
    const fetchAll = async () => {
      try {
        const [cfgR, stR, lgR] = await Promise.all([
          fetch('/data/forex/scalp_config.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
          fetch('/data/forex/scalp_status.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
          fetch('/data/forex/scalp_ledger.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
        ]);
        if (!alive) return;
        if (cfgR) setConfig(cfgR);
        if (stR) setStatus(stR);
        if (lgR) setLedger(lgR);
      } catch { /* silent */ }
    };
    fetchAll();
    const id = setInterval(fetchAll, mode === 'live' ? 5000 : 30000);
    return () => { alive = false; clearInterval(id); };
  }, [mode]);

  if (!config) {
    return (
      <Card className="p-8 text-center text-sm italic text-fg-muted">
        scalp engine config not yet published — the daemon may not be running, or publish_forex.sh hasn't run since it was added
      </Card>
    );
  }

  const g = config.global ?? {};
  const shadow = status?.shadow ?? g.shadow_mode ?? true;
  const enabled = g.enabled !== false;
  const pairs = config.pairs ?? {};
  const openCount = Object.keys(status?.open ?? {}).length;
  const haltedCount = Object.keys(status?.halted ?? {}).length;
  const lastStep = status?.last_step_utc;
  const lastStepAgeSec = lastStep
    ? Math.round((Date.now() - new Date(lastStep).getTime()) / 1000)
    : null;
  const heartbeatStale = lastStepAgeSec !== null && lastStepAgeSec > 60;

  const statsAll = ledger?.stats?.all;
  const statsToday = ledger?.stats?.today;
  const trades = (ledger?.trades ?? []).slice().reverse();   // newest first

  return (
    <Card className="overflow-hidden">
      {/* Top strip: engine + aggregate P/L */}
      <div className="grid grid-cols-2 gap-0 divide-x divide-y divide-line border-b border-line bg-ink-750/40 sm:grid-cols-6 sm:divide-y-0">
        <div className="p-4">
          <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">Engine</div>
          <div className="mt-1 flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${
              !enabled ? 'bg-fg-faint' : heartbeatStale ? 'bg-bear' : 'bg-bull animate-pulse-dot'
            }`} />
            <span className="text-sm font-medium text-fg">
              {!enabled ? 'off' : shadow ? 'shadow' : 'live'}
            </span>
          </div>
          <div className="mt-0.5 text-[10px] text-fg-subtle">
            {lastStepAgeSec !== null ? `${lastStepAgeSec}s ago` : 'no heartbeat'}
          </div>
        </div>

        <Stat label="Today" pips={statsToday?.net_pips} count={statsToday?.count} winRate={statsToday?.win_rate} />
        <Stat label="All-time" pips={statsAll?.net_pips} count={statsAll?.count} winRate={statsAll?.win_rate} />

        <div className="p-4">
          <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">Open</div>
          <div className="num mt-1 text-sm font-medium text-fg">{openCount}</div>
          <div className="mt-0.5 text-[10px] text-fg-subtle">of {g.max_concurrent ?? 2} max</div>
        </div>
        <div className="p-4">
          <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">Halted</div>
          <div className="num mt-1 text-sm font-medium text-fg">{haltedCount}</div>
          <div className="mt-0.5 text-[10px] text-fg-subtle">after 3 losses</div>
        </div>
        <div className="p-4">
          <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">Risk / max hold</div>
          <div className="num mt-1 text-sm font-medium text-fg">
            {((g.risk_pct_per_scalp ?? 0.005) * 100).toFixed(2)}%
          </div>
          <div className="mt-0.5 text-[10px] text-fg-subtle">{g.max_hold_minutes ?? 45}m cap</div>
        </div>
      </div>

      {/* Per-pair grid */}
      <div className="divide-y divide-line">
        {Object.entries(pairs).map(([epic, pc]) => {
          const enabled = pc.enabled === true;
          const halted = !!status?.halted?.[epic];
          const hasPos = !!status?.open?.[epic];
          return (
            <div key={epic} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-4 py-3 sm:grid-cols-[120px_90px_120px_1fr_auto]">
              <div>
                <div className="text-sm font-semibold text-fg">{PAIR_LABEL[epic as EpicCode] ?? epic}</div>
                <div className="mt-0.5 hidden text-[10px] uppercase tracking-wider text-fg-subtle sm:block">
                  {pc.mode ?? '—'}
                </div>
              </div>
              <div>
                {halted
                  ? <Badge tone="bear" size="xs">halted</Badge>
                  : enabled
                    ? <Badge tone={hasPos ? 'bull' : 'sky'} size="xs">{hasPos ? 'in trade' : 'active'}</Badge>
                    : <Badge tone="neutral" size="xs">off</Badge>}
              </div>
              <div className="hidden text-[11px] text-fg-muted num sm:block">
                {pc.bias ? `bias ${pc.bias}` : '—'}
              </div>
              <div className="hidden text-[11px] text-fg-muted sm:block">
                {pc.sessions?.length ? pc.sessions.join(' · ') : pc.reason ?? '—'}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-fg-subtle sm:hidden">
                {pc.mode ?? '—'} · {pc.bias ?? '—'}
              </div>
            </div>
          );
        })}
      </div>

      {/* Recent trades */}
      {trades.length > 0 && (
        <div className="border-t border-line">
          <div className="flex items-center justify-between px-4 py-2 bg-ink-750/30">
            <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">Recent trades · shadow</div>
            <div className="text-[10px] text-fg-subtle num">{trades.length} shown</div>
          </div>
          <div className="max-h-[320px] overflow-y-auto divide-y divide-line">
            {trades.map((t, i) => <TradeRow key={`${t.closed_at}-${i}`} t={t} />)}
          </div>
        </div>
      )}

      {/* Recent actions (in-flight events) */}
      {status?.actions && status.actions.length > 0 && (
        <div className="border-t border-line bg-ink-750/30 p-4">
          <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle mb-2">In-flight actions · this step</div>
          <div className="flex flex-wrap gap-2">
            {status.actions.slice(-5).map((a, i) => (
              <div key={i} className="rounded-md border border-line bg-ink-800 px-2.5 py-1 text-[11px] text-fg-muted">
                <span className="font-medium text-fg">{PAIR_LABEL[a.epic as EpicCode] ?? a.epic}</span>
                <span className="mx-1.5 text-fg-subtle">·</span>
                <span className={a.status === 'opened' ? 'text-bull' : a.status === 'closed' ? (a.pnl != null && a.pnl > 0 ? 'text-bull' : 'text-bear') : 'text-amber'}>
                  {a.status}{a.how ? ` (${a.how})` : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function Stat({ label, pips, count, winRate }: { label: string; pips?: number; count?: number; winRate?: number | null }) {
  const tone = pips == null ? 'text-fg-muted' : pips > 0 ? 'text-bull' : pips < 0 ? 'text-bear' : 'text-fg';
  return (
    <div className="p-4">
      <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">{label}</div>
      <div className={`num mt-1 text-sm font-medium ${tone}`}>
        {pips != null ? `${pips >= 0 ? '+' : ''}${pips.toFixed(1)}p` : '—'}
      </div>
      <div className="mt-0.5 text-[10px] text-fg-subtle num">
        {count != null && count > 0
          ? `${count} trades · ${winRate != null ? Math.round(winRate * 100) + '% WR' : '—'}`
          : 'no trades'}
      </div>
    </div>
  );
}

function TradeRow({ t }: { t: ScalpTrade }) {
  const dir = (t.direction ?? '').toUpperCase();
  const dirTone = dir === 'BUY' ? 'bull' : 'bear';
  const howTone =
    t.how === 'tp_hit' ? 'bg-bull/20 text-bull' :
    t.how === 'sl_hit' ? 'bg-bear/20 text-bear' :
    t.how === 'time_exit' ? 'bg-amber/20 text-amber' :
    'bg-ink-700 text-fg-muted';

  // Pips computed client-side for display
  const PIP: Record<string, number> = {
    EURUSD: 0.0001, GBPUSD: 0.0001, AUDUSD: 0.0001,
    USDCAD: 0.0001, USDCHF: 0.0001, USDJPY: 0.01,
    GOLD: 0.1, OIL_CRUDE: 0.01, BTCUSD: 1,
  };
  const pip = PIP[t.epic] ?? 0.0001;
  const raw = (t.entry != null && t.exit != null) ? (t.exit - t.entry) / pip : null;
  const pips = raw != null ? (dir === 'BUY' ? raw : -raw) : null;

  const dp = dpFor(t.epic as any);

  return (
    <div className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-3 px-4 py-2.5 text-[12px] hover:bg-ink-800/40">
      <div className="flex items-center gap-2">
        <Badge tone={dirTone} size="xs">{dir}</Badge>
        <div className="text-sm font-medium text-fg">{PAIR_LABEL[t.epic as EpicCode] ?? t.epic}</div>
      </div>
      <div className="flex items-baseline gap-2 min-w-0 num text-fg-muted">
        <span className="truncate">
          {fmtNum(t.entry, dp)} → {fmtNum(t.exit, dp)}
        </span>
        <span className="text-[10px] text-fg-subtle whitespace-nowrap">
          · {dateTimeIST(t.closed_at)} · {t.setup ?? '—'} · {t.held_min != null ? `${t.held_min.toFixed(1)}m` : '—'}
        </span>
      </div>
      <div className={`num text-[12px] font-medium ${pips == null ? 'text-fg-muted' : pips > 0 ? 'text-bull' : pips < 0 ? 'text-bear' : 'text-fg'}`}>
        {pips != null ? `${pips >= 0 ? '+' : ''}${pips.toFixed(1)}p` : '—'}
      </div>
      <div className={`rounded px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider ${howTone}`}>
        {t.how ?? '—'}
      </div>
    </div>
  );
}
