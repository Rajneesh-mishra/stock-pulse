import type { EpicCode } from '../types';

export const PIP_SIZE: Record<EpicCode, number> = {
  EURUSD: 0.0001, GBPUSD: 0.0001, AUDUSD: 0.0001,
  USDCAD: 0.0001, USDCHF: 0.0001,
  USDJPY: 0.01, GOLD: 0.1, OIL_CRUDE: 0.01, BTCUSD: 1.0,
};

export const PAIR_LABEL: Record<EpicCode, string> = {
  EURUSD: 'EUR/USD',
  GBPUSD: 'GBP/USD',
  AUDUSD: 'AUD/USD',
  USDCAD: 'USD/CAD',
  USDCHF: 'USD/CHF',
  USDJPY: 'USD/JPY',
  GOLD: 'XAU/USD',
  OIL_CRUDE: 'WTI',
  BTCUSD: 'BTC/USD',
};

export const PAIR_FULL: Record<EpicCode, string> = {
  EURUSD: 'Euro · US Dollar',
  GBPUSD: 'British Pound · US Dollar',
  AUDUSD: 'Australian Dollar · US Dollar',
  USDCAD: 'US Dollar · Canadian Dollar',
  USDCHF: 'US Dollar · Swiss Franc',
  USDJPY: 'US Dollar · Japanese Yen',
  GOLD: 'Gold Spot',
  OIL_CRUDE: 'WTI Crude Oil',
  BTCUSD: 'Bitcoin · US Dollar',
};

export const THEME_TAG: Record<EpicCode, string> = {
  EURUSD: 'dollar',
  GBPUSD: 'dollar',
  AUDUSD: 'risk',
  USDCAD: 'dollar · oil',
  USDCHF: 'dollar',
  USDJPY: 'intervention',
  GOLD: 'safe haven',
  OIL_CRUDE: 'geopolitics',
  BTCUSD: 'crypto',
};

export const INSTRUMENTS: EpicCode[] = Object.keys(PIP_SIZE) as EpicCode[];

export function dpFor(epic: EpicCode): number {
  if (epic === 'USDJPY' || epic === 'OIL_CRUDE') return 3;
  if (epic === 'GOLD') return 2;
  if (epic === 'BTCUSD') return 1;
  return 5;
}

export function fmtNum(n: number | null | undefined, dp: number): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return Number(n).toLocaleString(undefined, {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
}

export function fmtMoney(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '$—';
  const v = Number(n);
  const sign = v < 0 ? '−' : '';
  return `${sign}$${Math.abs(v).toLocaleString(undefined, {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })}`;
}

export function fmtPct(pct: number | null | undefined, dp = 2): string {
  if (pct === null || pct === undefined || Number.isNaN(Number(pct))) return '—';
  const v = Number(pct);
  return `${v >= 0 ? '+' : ''}${v.toFixed(dp)}%`;
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 5) return 'now';
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function shortTimestamp(iso?: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toISOString().slice(11, 19);
}

export function truncate(s: string | null | undefined, n: number): string {
  if (!s) return '';
  const t = s.trim();
  if (t.length <= n) return t;
  return t.slice(0, n).trim() + '…';
}

export function collapseWhitespace(s: string | null | undefined): string {
  if (!s) return '';
  return s.replace(/\s+/g, ' ').trim();
}

export function stripRegimePrefix(s: string | null | undefined): string {
  if (!s) return '';
  return s
    .replace(/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*UTC(?:\s+[^—]+)?\s*—\s*/, '')
    .trim();
}
