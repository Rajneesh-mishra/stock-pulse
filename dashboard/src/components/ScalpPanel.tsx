import { useEffect, useState } from 'react';
import { Card } from './Card';
import { Badge } from './Badge';
import { PAIR_LABEL } from '../lib/format';
import type { EpicCode, Mode } from '../types';

interface ScalpConfig {
  global?: {
    enabled?: boolean;
    shadow_mode?: boolean;
    daily_loss_cap_usd?: number;
    max_concurrent?: number;
    risk_pct_per_scalp?: number;
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

export function ScalpPanel({ mode }: { mode: Mode }) {
  const [config, setConfig] = useState<ScalpConfig | null>(null);
  const [status, setStatus] = useState<ScalpStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const fetchAll = async () => {
      try {
        const [cfgR, stR] = await Promise.all([
          fetch('/data/forex/scalp_config.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
          fetch('/data/forex/scalp_status.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
        ]);
        if (!alive) return;
        if (cfgR) setConfig(cfgR);
        if (stR) setStatus(stR);
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
  const pnl = Number(status?.daily_pnl_usd ?? 0);
  const pairs = config.pairs ?? {};
  const openCount = Object.keys(status?.open ?? {}).length;
  const haltedCount = Object.keys(status?.halted ?? {}).length;
  const lastStep = status?.last_step_utc;
  const lastStepAgeSec = lastStep
    ? Math.round((Date.now() - new Date(lastStep).getTime()) / 1000)
    : null;
  const heartbeatStale = lastStepAgeSec !== null && lastStepAgeSec > 60;

  return (
    <Card className="overflow-hidden">
      {/* Top strip: global engine state */}
      <div className="grid grid-cols-2 gap-0 divide-x divide-line border-b border-line bg-ink-750/40 sm:grid-cols-5">
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
        <div className="p-4">
          <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">Today P/L</div>
          <div className={`num mt-1 text-sm font-medium ${pnl > 0 ? 'text-bull' : pnl < 0 ? 'text-bear' : 'text-fg'}`}>
            {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
          </div>
          <div className="mt-0.5 text-[10px] text-fg-subtle">cap ${g.daily_loss_cap_usd ?? 15}</div>
        </div>
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
        <div className="col-span-2 p-4 sm:col-span-1">
          <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">Risk per scalp</div>
          <div className="num mt-1 text-sm font-medium text-fg">
            {((g.risk_pct_per_scalp ?? 0.005) * 100).toFixed(2)}%
          </div>
          <div className="mt-0.5 text-[10px] text-fg-subtle">half of swing</div>
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

      {/* Recent actions footer */}
      {status?.actions && status.actions.length > 0 && (
        <div className="border-t border-line bg-ink-750/30 p-4">
          <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle mb-2">Recent actions</div>
          <div className="flex flex-wrap gap-2">
            {status.actions.slice(-5).map((a, i) => (
              <div key={i} className="rounded-md border border-line bg-ink-800 px-2.5 py-1 text-[11px] text-fg-muted">
                <span className="font-medium text-fg">{PAIR_LABEL[a.epic as EpicCode] ?? a.epic}</span>
                <span className="mx-1.5 text-fg-subtle">·</span>
                <span className={a.status === 'opened' ? 'text-bull' : a.status === 'closed' ? (a.pnl != null && a.pnl > 0 ? 'text-bull' : 'text-bear') : 'text-amber'}>
                  {a.status}{a.how ? ` (${a.how})` : ''}{a.pnl != null ? ` $${a.pnl.toFixed(2)}` : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
