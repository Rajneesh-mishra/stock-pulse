import { useEffect, useMemo, useRef, useState } from 'react';
import type { ForexEvent, LiveTick, EpicCode, Snapshot } from './types';
import { useMode } from './hooks/useMode';
import { useSnapshot } from './hooks/useSnapshot';
import { useTickStream } from './hooks/useTickStream';
import { useEventStream } from './hooks/useEventStream';
import { useCandles } from './hooks/useCandles';
import { StatusPill } from './components/StatusPill';
import { BinaryEventHero } from './components/BinaryEventHero';
import { CapitalStrip } from './components/CapitalStrip';
import { SectionHeader } from './components/SectionHeader';
import { InstrumentGrid } from './components/InstrumentGrid';
import { AlertList } from './components/AlertList';
import { GatesTable } from './components/GatesTable';
import { CounterfactualTable } from './components/CounterfactualTable';
import { PositionsPanel } from './components/PositionsPanel';
import { EventLog } from './components/EventLog';
import { RegimeCallout } from './components/RegimeCallout';
import { relativeTime } from './lib/format';

type NavId = 'overview' | 'pairs' | 'alerts' | 'history' | 'events';

const NAV: { id: NavId; label: string; icon: string }[] = [
  { id: 'overview', label: 'Overview', icon: '◉' },
  { id: 'pairs',    label: 'Pairs',    icon: '▦' },
  { id: 'alerts',   label: 'Alerts',   icon: '◆' },
  { id: 'history',  label: 'History',  icon: '❙❙' },
  { id: 'events',   label: 'Events',   icon: '≡' },
];

export default function App() {
  const mode = useMode();
  const snap = useSnapshot(mode);
  const ticks = useTickStream(mode, (snap.snapshot?.live_ticks as any));
  const events = useEventStream(mode, 60);
  const candles = useCandles(mode);

  const [section, setSection] = useState<NavId>('overview');

  // Smooth-scroll the tapped section into view (mobile) or scroll-spy (desktop)
  const refs: Record<NavId, React.RefObject<HTMLDivElement>> = {
    overview: useRef(null), pairs: useRef(null), alerts: useRef(null),
    history: useRef(null), events: useRef(null),
  };

  const go = (id: NavId) => {
    setSection(id);
    const el = refs[id].current;
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Scroll-spy for active nav highlighting
  useEffect(() => {
    const observer = new IntersectionObserver(
      entries => {
        const visible = entries
          .filter(e => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]) {
          const id = (visible[0].target as HTMLElement).dataset.section as NavId;
          if (id) setSection(id);
        }
      },
      { rootMargin: '-30% 0px -60% 0px', threshold: [0, 0.25, 0.5] },
    );
    Object.values(refs).forEach(r => r.current && observer.observe(r.current));
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const state = snap.snapshot?.state_file ?? {};
  const tradeHistory = state.trade_history ?? [];
  const openPositions = snap.snapshot?.broker?.positions?.positions ?? state.open_positions ?? [];
  const alerts = snap.watchlist?.level_alerts ?? [];
  const tickMap = ticks.ticks as Record<EpicCode, LiveTick & { flashDir?: 'up' | 'dn' }>;
  const wsReceived = snap.snapshot?.ws_stats?.ticks_received ?? ticks.received;
  const lastTick = state.last_tick_utc ?? state.last_tick ?? snap.snapshot?.ts;

  return (
    <div className="min-h-screen pb-24 sm:pb-8">
      {/* Top bar */}
      <header className="sticky top-0 z-40 border-b border-line bg-ink-900/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1440px] items-center justify-between px-4 py-3 sm:px-8">
          <div className="flex items-center gap-3">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-bull to-sky text-ink-900 shadow-glow">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M2 10l3-3 3 2 3-5 3 4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <div className="leading-tight">
              <div className="text-[15px] font-semibold tracking-tight text-fg">Stock Pulse</div>
              <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">Forex Command</div>
            </div>
          </div>

          {/* Desktop nav */}
          <nav className="hidden items-center gap-1 lg:flex">
            {NAV.map(n => (
              <button
                key={n.id}
                onClick={() => go(n.id)}
                className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors ${
                  section === n.id ? 'bg-ink-750 text-fg' : 'text-fg-muted hover:text-fg'
                }`}
              >
                {n.label}
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-3">
            <span className="hidden text-[11px] num text-fg-subtle sm:inline">
              tick {relativeTime(lastTick)}
            </span>
            <StatusPill mode={mode} ticks={wsReceived} />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] px-4 pt-6 sm:px-8 sm:pt-8">

        {/* ─────────── OVERVIEW ─────────── */}
        <section ref={refs.overview} data-section="overview" className="rise space-y-6" style={{ animationDelay: '0ms' }}>
          <BinaryEventHero event={state.binary_event} />
          <CapitalStrip snapshot={snap.snapshot} />
        </section>

        {/* ─────────── PAIRS ─────────── */}
        <section ref={refs.pairs} data-section="pairs" className="rise mt-10" style={{ animationDelay: '80ms' }}>
          <SectionHeader
            title="Watched pairs"
            caption="Real-time quotes · M15 candles · tick flash on price change"
            right={mode === 'live' ? <span className="text-bull">● WS</span> : <span>static</span>}
          />
          <InstrumentGrid snapshot={snap.snapshot} ticks={tickMap} candles={candles} alerts={alerts} />
        </section>

        {/* ─────────── SIGNAL READOUT ─────────── */}
        <section className="rise mt-10" style={{ animationDelay: '120ms' }}>
          <SectionHeader
            title="Signal readout"
            caption="Inputs feeding the conviction × R:R sizing call — not a pass/fail gate"
          />
          <GatesTable rows={snap.gates} />
        </section>

        {/* ─────────── ALERTS ─────────── */}
        <section ref={refs.alerts} data-section="alerts" className="rise mt-10" style={{ animationDelay: '160ms' }}>
          <SectionHeader
            title="Armed triggers"
            caption="Each watchlist alert — proximity, thesis, and counterfactual hit-rate by horizon"
          />
          <AlertList alerts={alerts} counterfactual={snap.counterfactual} ticks={tickMap as any} />
        </section>

        <section className="rise mt-10" style={{ animationDelay: '200ms' }}>
          <SectionHeader
            title="What would've happened"
            caption="Every alert fired, tracked +1h / +4h / +24h — SKIP decisions made auditable"
          />
          <CounterfactualTable cf={snap.counterfactual} />
        </section>

        {/* ─────────── HISTORY ─────────── */}
        <section ref={refs.history} data-section="history" className="rise mt-10" style={{ animationDelay: '240ms' }}>
          <SectionHeader title="Positions & trades" caption="Open risk, closed trades, and the lessons on each" />
          <PositionsPanel positions={openPositions} trades={tradeHistory} />
        </section>

        <section className="rise mt-10" style={{ animationDelay: '280ms' }}>
          <SectionHeader title="The read" caption="Regime note — what the orchestrator is working from" />
          <RegimeCallout note={state.regime_note} stamp={state.last_tick_utc ?? state.last_tick} />
        </section>

        {/* ─────────── EVENTS ─────────── */}
        <section ref={refs.events} data-section="events" className="rise mt-10" style={{ animationDelay: '320ms' }}>
          <SectionHeader
            title="Live events"
            caption="Everything the daemons emit — bar closes, structure shifts, news flashes, level crosses"
          />
          <EventLog events={events} />
        </section>

        <footer className="mt-16 border-t border-line pt-6 pb-4 text-[10px] uppercase tracking-[0.18em] text-fg-subtle sm:flex sm:items-center sm:justify-between">
          <div>Stock Pulse · Forex Command</div>
          <div className="mt-2 sm:mt-0">
            <a className="text-fg-muted hover:text-fg" href="https://github.com/Rajneesh-mishra/stock-pulse" target="_blank" rel="noopener">
              github ↗
            </a>
          </div>
        </footer>
      </main>

      {/* Mobile bottom-tab nav */}
      <nav className="fixed bottom-0 left-0 right-0 z-50 border-t border-line bg-ink-900/90 backdrop-blur-xl lg:hidden">
        <div className="mx-auto grid max-w-md grid-cols-5">
          {NAV.map(n => (
            <button
              key={n.id}
              onClick={() => go(n.id)}
              className={`flex flex-col items-center gap-0.5 py-2 text-[10px] font-medium uppercase tracking-wider transition-colors ${
                section === n.id ? 'text-bull' : 'text-fg-subtle'
              }`}
            >
              <span className="text-[14px]">{n.icon}</span>
              <span>{n.label}</span>
            </button>
          ))}
        </div>
      </nav>
    </div>
  );
}
