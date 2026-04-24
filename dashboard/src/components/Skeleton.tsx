/**
 * Skeleton loader primitives. Tailwind's animate-pulse is too aggressive
 * for an info-dense dashboard; we use a subtler shimmer via a custom
 * gradient animation (configured in tailwind.config.js).
 *
 * Rule: skeletons mimic the layout of the real content so the page doesn't
 * jump when data arrives. A "1-line pill" skeleton for a single number, a
 * block for a chart area, etc.
 */
import type { HTMLAttributes, ReactNode } from 'react';

type SkProps = HTMLAttributes<HTMLDivElement> & {
  w?: string;
  h?: string;
  className?: string;
  rounded?: 'sm' | 'md' | 'lg' | 'full';
};

function base({ w, h, className = '', rounded = 'md' }: SkProps) {
  const r = rounded === 'full' ? 'rounded-full'
          : rounded === 'lg' ? 'rounded-lg'
          : rounded === 'sm' ? 'rounded-sm'
          : 'rounded-md';
  return `relative overflow-hidden bg-ink-750 ${r} ${className}`;
}

export function SkBar({ w = 'w-24', h = 'h-3', className = '', rounded = 'sm' }: SkProps) {
  return (
    <div className={`${base({ className, rounded })} ${w} ${h}`}>
      <span className="absolute inset-0 -translate-x-full animate-skeleton bg-gradient-to-r from-transparent via-white/5 to-transparent" />
    </div>
  );
}

export function SkBlock({ w = 'w-full', h = 'h-12', className = '', rounded = 'md' }: SkProps) {
  return (
    <div className={`${base({ className, rounded })} ${w} ${h}`}>
      <span className="absolute inset-0 -translate-x-full animate-skeleton bg-gradient-to-r from-transparent via-white/5 to-transparent" />
    </div>
  );
}

/** Wrap any content — renders the skeleton when `loading` is true, else the children. */
export function SkWrap({ loading, children, skeleton }: { loading: boolean; children: ReactNode; skeleton: ReactNode }) {
  return loading ? <>{skeleton}</> : <>{children}</>;
}

/** Hero-sized skeleton for the binary-event card. */
export function SkHero() {
  return (
    <div className="relative overflow-hidden rounded-xl2 border border-line bg-ink-800 p-6 sm:p-8 lg:p-10">
      <div className="absolute inset-0 grid-bg" aria-hidden="true" />
      <div className="relative space-y-5">
        <div className="flex items-center gap-2">
          <SkBar w="w-14" h="h-4" rounded="sm" />
          <SkBar w="w-32" h="h-3" rounded="sm" />
        </div>
        <div className="space-y-3">
          <SkBar w="w-[80%]" h="h-8 sm:h-10" />
          <SkBar w="w-[55%]" h="h-8 sm:h-10" />
        </div>
        <div className="mt-6 flex flex-wrap items-end gap-4 sm:gap-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex flex-col gap-2">
              <SkBlock w="w-16 sm:w-20" h="h-12 sm:h-16" rounded="lg" />
              <SkBar w="w-10" h="h-2" />
            </div>
          ))}
        </div>
        <div className="mt-6 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line pt-5 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="space-y-2">
              <SkBar w="w-16" h="h-2" />
              <SkBar w="w-24" h="h-3" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Capital-strip — 4 metric cells. */
export function SkCapitalStrip() {
  return (
    <div className="rounded-xl2 border border-line bg-ink-800 shadow-card overflow-hidden">
      <div className="grid grid-cols-2 divide-y divide-x divide-line sm:grid-cols-4 sm:divide-y-0">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex flex-col gap-2 p-5 sm:p-6">
            <SkBar w="w-20" h="h-2" />
            <SkBar w="w-28" h="h-7" />
            <SkBar w="w-32" h="h-2" />
          </div>
        ))}
      </div>
    </div>
  );
}

/** Instrument grid — N cards. */
export function SkInstrumentGrid({ count = 9 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-xl2 border border-line bg-ink-800 p-4 sm:p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div className="space-y-1.5">
              <SkBar w="w-16" h="h-3" />
              <SkBar w="w-12" h="h-2" />
            </div>
            <SkBar w="w-20" h="h-5" rounded="sm" />
          </div>
          <SkBar w="w-24" h="h-7" />
          <SkBlock w="w-full" h="h-12" rounded="md" />
          <div className="grid grid-cols-3 gap-2 pt-2">
            {Array.from({ length: 6 }).map((_, j) => (
              <SkBar key={j} w="w-full" h="h-2" />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Alert card. */
export function SkAlertList({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-xl2 border border-line bg-ink-800 p-5 sm:p-6">
          <div className="grid gap-5 lg:grid-cols-[260px_1fr_220px]">
            <div className="space-y-3">
              <SkBar w="w-16" h="h-5" rounded="sm" />
              <SkBar w="w-32" h="h-8" />
              <SkBar w="w-48" h="h-3" />
              <SkBar w="w-full" h="h-2" rounded="full" />
            </div>
            <div className="space-y-2">
              <SkBar w="w-14" h="h-2" />
              <SkBar w="w-full" h="h-3" />
              <SkBar w="w-full" h="h-3" />
              <SkBar w="w-[80%]" h="h-3" />
            </div>
            <div className="space-y-3 lg:border-l lg:border-line lg:pl-6">
              {Array.from({ length: 4 }).map((_, j) => (
                <div key={j} className="flex justify-between">
                  <SkBar w="w-16" h="h-2" />
                  <SkBar w="w-10" h="h-2" />
                </div>
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/** Gates / signal readout. */
export function SkGatesTable() {
  return (
    <div className="rounded-xl2 border border-line bg-ink-800 shadow-card overflow-hidden">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="grid grid-cols-[130px_90px_90px_1fr_80px] items-center gap-4 border-b border-line px-5 py-4 last:border-0">
          <SkBar w="w-20" h="h-3" />
          <SkBar w="w-14" h="h-3" />
          <SkBar w="w-14" h="h-3" />
          <div className="flex gap-1">
            {Array.from({ length: 7 }).map((_, j) => (
              <SkBar key={j} w="w-5" h="h-2" rounded="sm" />
            ))}
          </div>
          <SkBar w="w-16" h="h-5" rounded="sm" />
        </div>
      ))}
    </div>
  );
}

/** Attention matrix — 9 pair rows. */
export function SkAttentionMatrix() {
  return (
    <div className="rounded-xl2 border border-line bg-ink-800 shadow-card overflow-hidden">
      {Array.from({ length: 9 }).map((_, i) => (
        <div key={i} className="grid grid-cols-[110px_auto_auto_1fr] items-center gap-3 border-b border-line px-5 py-3 last:border-0">
          <SkBar w="w-20" h="h-3" />
          <SkBar w="w-12" h="h-5" rounded="sm" />
          <SkBar w="w-12" h="h-5" rounded="sm" />
          <SkBar w="w-[80%]" h="h-2" />
        </div>
      ))}
    </div>
  );
}

/** Scalp engine panel skeleton. */
export function SkScalpPanel() {
  return (
    <div className="rounded-xl2 border border-line bg-ink-800 shadow-card overflow-hidden">
      <div className="grid grid-cols-2 divide-x divide-y divide-line sm:grid-cols-6 sm:divide-y-0">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="space-y-1.5 p-4">
            <SkBar w="w-16" h="h-2" />
            <SkBar w="w-20" h="h-4" />
            <SkBar w="w-14" h="h-2" />
          </div>
        ))}
      </div>
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="grid grid-cols-[120px_90px_120px_1fr] items-center gap-3 border-t border-line px-4 py-3">
          <SkBar w="w-20" h="h-3" />
          <SkBar w="w-14" h="h-4" rounded="sm" />
          <SkBar w="w-16" h="h-3" />
          <SkBar w="w-[80%]" h="h-2" />
        </div>
      ))}
    </div>
  );
}

/** Counterfactual heatmap cells. */
export function SkCounterfactual() {
  return (
    <div className="rounded-xl2 border border-line bg-ink-800 shadow-card overflow-hidden">
      <div className="grid grid-cols-[1fr_70px_70px_80px] sm:grid-cols-[2fr_1fr_1fr_1fr]">
        <div className="bg-ink-750/40 p-3"><SkBar w="w-16" h="h-2" /></div>
        <div className="bg-ink-750/40 p-3"><SkBar w="w-8" h="h-2" /></div>
        <div className="bg-ink-750/40 p-3"><SkBar w="w-8" h="h-2" /></div>
        <div className="bg-ink-750/40 p-3"><SkBar w="w-8" h="h-2" /></div>
        {Array.from({ length: 5 }).map((_, row) => (
          <div key={row} className="contents">
            <div className="border-t border-line p-3 space-y-1.5">
              <SkBar w="w-32" h="h-3" />
              <SkBar w="w-24" h="h-2" />
            </div>
            {Array.from({ length: 3 }).map((_, c) => (
              <div key={c} className="border-t border-line p-4 flex items-center justify-center">
                <SkBar w="w-12" h="h-3" />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Trades ledger rows. */
export function SkTradeRows({ count = 3 }: { count?: number }) {
  return (
    <div className="rounded-xl2 border border-line bg-ink-800 shadow-card overflow-hidden">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="grid grid-cols-[60px_1fr_auto] items-center gap-4 border-b border-line px-5 py-4 last:border-0">
          <SkBar w="w-8" h="h-8" rounded="full" />
          <div className="space-y-1.5">
            <SkBar w="w-32" h="h-4" />
            <SkBar w="w-48" h="h-2" />
          </div>
          <SkBar w="w-20" h="h-6" />
        </div>
      ))}
    </div>
  );
}

/** Event-log rows. */
export function SkEventLog({ count = 6 }: { count?: number }) {
  return (
    <div className="rounded-xl2 border border-line bg-ink-800 shadow-card overflow-hidden">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="grid grid-cols-[64px_110px_90px_1fr] items-baseline gap-4 border-b border-line px-5 py-3 last:border-0">
          <SkBar w="w-14" h="h-3" />
          <SkBar w="w-20" h="h-3" rounded="sm" />
          <SkBar w="w-14" h="h-3" />
          <SkBar w="w-full" h="h-2" />
        </div>
      ))}
    </div>
  );
}

/**
 * Top-of-page progress bar. Shows an indeterminate sweep while data is
 * loading, then fades to a subtle green tick when ready. Sticky above the
 * topbar.
 */
export function TopProgress({ loading }: { loading: boolean }) {
  return (
    <div className="fixed top-0 left-0 right-0 z-50 h-[2px] bg-transparent pointer-events-none">
      {loading ? (
        <div className="h-full w-full overflow-hidden">
          <div className="h-full w-1/3 animate-topbar-progress bg-gradient-to-r from-transparent via-bull to-transparent" />
        </div>
      ) : null}
    </div>
  );
}
