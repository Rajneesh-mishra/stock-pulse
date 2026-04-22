import { Card } from './Card';
import { collapseWhitespace, stripRegimePrefix, truncate } from '../lib/format';

export function RegimeCallout({ note, stamp }: { note?: string; stamp?: string }) {
  const clean = collapseWhitespace(stripRegimePrefix(note));
  const body = truncate(clean, 900);

  return (
    <Card className="relative overflow-hidden p-6 sm:p-8">
      <div className="pointer-events-none absolute -right-8 -top-8 h-40 w-40 rounded-full bg-bull/10 blur-3xl" aria-hidden="true" />
      <div className="pointer-events-none absolute -left-12 bottom-0 h-48 w-48 rounded-full bg-sky/10 blur-3xl" aria-hidden="true" />

      <div className="relative">
        <div className="mb-4 flex items-center gap-3">
          <div className="h-1 w-8 rounded-full bg-bull" />
          <div className="text-[10px] font-medium uppercase tracking-[0.22em] text-fg-subtle">current narrative</div>
        </div>
        {body ? (
          <p className="max-w-[72ch] text-[15.5px] leading-relaxed text-fg sm:text-base">{body}</p>
        ) : (
          <p className="text-sm italic text-fg-muted">No regime note recorded.</p>
        )}
        {stamp && (
          <div className="mt-5 flex items-center gap-2 text-[10px] uppercase tracking-wider text-fg-subtle">
            <span>{stamp}</span>
            <span>·</span>
            <span>claude orchestrator</span>
          </div>
        )}
      </div>
    </Card>
  );
}
