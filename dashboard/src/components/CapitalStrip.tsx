import type { Snapshot } from '../types';
import { fmtMoney } from '../lib/format';
import { Metric } from './Metric';
import { Card } from './Card';

export function CapitalStrip({ snapshot }: { snapshot: Snapshot | null }) {
  const sf = snapshot?.state_file ?? {};
  const acct = snapshot?.broker?.account;
  const positions = snapshot?.broker?.positions?.positions ?? [];

  const bal = acct?.balance ?? sf.broker_balance ?? 1000;
  const dailyPnl = Number(acct?.profit_loss ?? sf.daily_pnl ?? 0);
  const totalPnl = Number(sf.total_pnl ?? (sf.trade_history ?? []).reduce((s, t) => s + (Number(t.pnl) || 0), 0));
  const trades = (sf.trade_history ?? []).length;
  const open = positions.length;
  const openRisk = positions.reduce((s: number, p: any) => {
    const pos = p.position ?? p;
    if (pos?.stopLevel && pos?.level) {
      return s + Math.abs(Number(pos.level) - Number(pos.stopLevel)) * Number(pos.size || 0);
    }
    return s;
  }, 0);
  const riskPct = bal > 0 ? (openRisk / bal) * 100 : 0;

  return (
    <Card className="overflow-hidden">
      <div className="grid grid-cols-2 divide-y divide-x divide-line sm:grid-cols-4 sm:divide-y-0">
        <Metric
          label="Balance"
          value={fmtMoney(bal)}
          sub={`total P/L ${fmtMoney(totalPnl)}`}
        />
        <Metric
          label="Today's P&L"
          value={fmtMoney(dailyPnl)}
          sub={dailyPnl === 0 ? 'flat today' : `${trades} closed lifetime`}
          tone={dailyPnl > 0 ? 'bull' : dailyPnl < 0 ? 'bear' : 'default'}
        />
        <Metric
          label="Open Risk"
          value={openRisk > 0 ? fmtMoney(openRisk) : 'at rest'}
          sub={`${riskPct.toFixed(2)}% used · cap 6%`}
          tone={openRisk === 0 ? 'muted' : 'default'}
        />
        <Metric
          label="Positions"
          value={open > 0 ? String(open) : 'none'}
          sub={`${open}/4 open`}
          tone={open === 0 ? 'muted' : 'bull'}
        />
      </div>
    </Card>
  );
}
