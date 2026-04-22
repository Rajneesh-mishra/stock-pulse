// Shapes produced by the Python backend — all fields optional since
// we gracefully degrade when running against static JSON on GitHub Pages.

export type EpicCode =
  | 'EURUSD' | 'USDJPY' | 'GOLD' | 'OIL_CRUDE' | 'BTCUSD'
  | 'AUDUSD' | 'USDCAD' | 'GBPUSD' | 'USDCHF';

export interface BinaryEvent {
  name?: string;
  deadline_utc?: string | null;
  active?: boolean;
  verified?: boolean;
  sources?: string[];
  note?: string;
}

export interface Trade {
  instrument: EpicCode;
  direction: 'BUY' | 'SELL' | string;
  entry_price?: number;
  exit_price?: number;
  pnl?: number;
  result?: string;
  opened_at?: string;
  closed_at?: string;
  exit_reason?: string;
  lessons?: string;
}

export interface LevelAlert {
  id: string;
  instrument: EpicCode;
  level: number;
  direction: 'buy' | 'sell';
  note?: string;
  current_price_ref?: number;
  proximity?: string;
  last_updated?: string;
  cooldown_sec?: number;
  emit_on?: string;
}

export interface Watchlist {
  level_alerts?: LevelAlert[];
  structure_watch?: { instrument: EpicCode; timeframes: string[] }[];
  instruments?: EpicCode[];
  alerts?: LevelAlert[];  // live API shape
}

export interface CounterfactualAlert {
  alert_id: string;
  instrument: EpicCode;
  direction: string | null;
  fires: number;
  by_horizon: Record<'1h'|'4h'|'24h', {
    filled: number;
    favorable: number;
    avg_pips: number | null;
    hit_rate: number | null;
  }>;
}

export interface CounterfactualSummary {
  generated_at?: string;
  alerts?: CounterfactualAlert[];
}

export interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface LiveTick {
  epic: EpicCode;
  bid: number;
  ofr: number;
  ts_ms: number;
  rcv_ts?: number;
}

export interface GateInfo {
  id: number;
  name: string;
  status: 'PASS' | 'FAIL' | 'SOFT' | string;
  value?: number;
  detail?: string;
}

export interface GateRow {
  epic: EpicCode;
  ts: string;
  mid?: number;
  atr_m15?: number;
  verdict: 'SKIP' | 'HALF' | 'FULL' | 'ENTER' | string;
  pass_count: number;
  gates: GateInfo[];
}

export interface BrokerAccount {
  balance?: number;
  available?: number;
  profit_loss?: number;
  currency?: string;
}

export interface BrokerSnapshot {
  account?: BrokerAccount;
  positions?: { count: number; positions: any[] };
  prices?: Record<EpicCode, {
    epic: string; bid: number; offer: number; spread?: number;
    high?: number; low?: number; change_pct?: number; update_time?: string;
  }>;
}

export interface DaemonInfo {
  name: string;
  loaded: boolean;
  pid?: number;
  status?: number;
  control?: string;
  status_detail?: any;
  last_poll_age_sec?: number;
}

export interface Snapshot {
  ts?: string;
  daemons?: DaemonInfo[];
  broker?: BrokerSnapshot;
  watchlist?: Watchlist;
  events_total?: number;
  events_unconsumed?: number;
  state_file?: {
    regime?: string;
    regime_note?: string;
    last_tick?: string;
    last_tick_utc?: string;
    daily_pnl?: number;
    total_pnl?: number;
    total_trades?: number;
    consecutive_losses?: number;
    binary_event?: BinaryEvent;
    trade_history?: Trade[];
    open_positions?: any[];
    broker_balance?: number;
  };
  live_ticks?: Record<EpicCode, LiveTick>;
  ws_stats?: {
    status?: string;
    reconnects?: number;
    ticks_received?: number;
    last_tick_at?: string;
    subscribed_epics?: EpicCode[];
    last_error?: string | null;
  };
}

export interface ForexEvent {
  event_id?: string;
  type?: string;
  instrument?: EpicCode;
  alert_id?: string;
  timeframe?: string;
  ts_utc?: string;
  payload?: any;
  headline?: string;
  body?: string;
  query_id?: string;
  consumed_by_claude?: boolean;
}

export type Mode = 'live' | 'static' | 'connecting';
