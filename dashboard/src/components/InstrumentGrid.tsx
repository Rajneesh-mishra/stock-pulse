import type { EpicCode, Candle, LevelAlert, LiveTick, Snapshot } from '../types';
import { INSTRUMENTS } from '../lib/format';
import { InstrumentCard } from './InstrumentCard';

type Props = {
  snapshot: Snapshot | null;
  ticks: Record<EpicCode, LiveTick & { flashDir?: 'up' | 'dn' }>;
  candles: Record<EpicCode, Candle[]>;
  alerts: LevelAlert[];
};

export function InstrumentGrid({ snapshot, ticks, candles, alerts }: Props) {
  const byInst: Record<EpicCode, LevelAlert[]> = {} as any;
  for (const a of alerts) {
    const arr = byInst[a.instrument] ?? [];
    arr.push(a);
    byInst[a.instrument] = arr;
  }
  const snapPrices = snapshot?.broker?.prices ?? ({} as any);

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {INSTRUMENTS.map(epic => (
        <InstrumentCard
          key={epic}
          epic={epic}
          tick={ticks[epic] ?? null}
          snapshotPrice={snapPrices[epic]}
          candles={candles[epic]}
          alert={(byInst[epic] ?? [])[0]}
        />
      ))}
    </div>
  );
}
