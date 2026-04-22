import type { ForexEvent } from '../types';
import { Card } from './Card';
import { shortTimestamp, truncate, collapseWhitespace } from '../lib/format';

const TYPE_TONE: Record<string, string> = {
  level_enter:     'text-bull border-bull/40',
  level_cross:     'text-bull border-bull/40',
  level_exit:      'text-bear border-bear/40',
  news_flash:      'text-amber border-amber/40',
  structure_bos:   'text-sky border-sky/40',
  structure_choch: 'text-sky border-sky/40',
  bar_close:       'text-fg-subtle border-line',
  position_opened: 'text-bull border-bull/40',
  position_closed: 'text-violet border-violet/40',
  trail_candidate: 'text-amber border-amber/40',
  volatility_spike: 'text-violet border-violet/40',
  daily_pnl_threshold: 'text-bear border-bear/40',
};

function eventBlurb(ev: ForexEvent): string {
  const parts: string[] = [];
  if (ev.alert_id) parts.push(ev.alert_id);
  if (ev.timeframe) parts.push(ev.timeframe);
  const p = ev.payload || {};
  if (p.price != null) parts.push(`@ ${p.price}`);
  if (p.note) parts.push(collapseWhitespace(p.note));
  if (ev.headline) parts.push(collapseWhitespace(ev.headline));
  return truncate(parts.join(' · '), 160);
}

export function EventLog({ events }: { events: ForexEvent[] }) {
  if (!events.length) {
    return (
      <Card className="p-10 text-center">
        <div className="text-sm italic text-fg-muted">Waiting for events…</div>
      </Card>
    );
  }

  return (
    <Card className="max-h-[480px] overflow-y-auto">
      <ul className="divide-y divide-line">
        {events.map(ev => {
          const tone = TYPE_TONE[ev.type ?? ''] ?? 'text-fg-muted border-line';
          return (
            <li key={ev.event_id ?? Math.random()} className="animate-event-in grid grid-cols-[56px_1fr] gap-3 px-4 py-3 sm:grid-cols-[64px_110px_90px_1fr] sm:gap-4 sm:px-5">
              <span className="num text-[11px] font-medium text-amber">{shortTimestamp(ev.ts_utc)}</span>
              <span className={`hidden rounded-md border px-2 py-0.5 text-center text-[10px] font-medium uppercase tracking-wider sm:inline-block ${tone}`}>
                {ev.type ?? 'event'}
              </span>
              <span className="hidden truncate text-[11px] font-semibold text-fg sm:inline">{ev.instrument ?? ''}</span>
              <span className="truncate text-[12px] text-fg-muted">
                <span className="sm:hidden mr-2 text-[10px] uppercase tracking-wider text-fg-subtle">{ev.type ?? 'event'}</span>
                <span className="sm:hidden mr-2 font-semibold text-fg">{ev.instrument ?? ''}</span>
                {eventBlurb(ev)}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
