import { useMemo, useId } from 'react';
import { sparklinePath } from '../lib/sparkline';

type Props = {
  values: number[];
  width?: number;
  height?: number;
  /** force a direction color; otherwise derived from first vs last */
  dir?: 'up' | 'dn' | 'flat';
  className?: string;
};

export function Sparkline({ values, width = 200, height = 48, dir, className = '' }: Props) {
  const gradId = useId().replace(/:/g, '');
  const { line, area, dir: computed } = useMemo(
    () => sparklinePath(values, width, height, 2),
    [values, width, height],
  );
  const finalDir = dir ?? computed;

  const stroke =
    finalDir === 'up' ? '#22D7A0' :
    finalDir === 'dn' ? '#F43F5E' :
    '#6B7078';
  const fillStop =
    finalDir === 'up' ? 'rgba(34,215,160,0.28)' :
    finalDir === 'dn' ? 'rgba(244,63,94,0.28)' :
    'rgba(237,238,240,0.12)';

  if (!values || values.length < 2) {
    return (
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
           className={`block w-full ${className}`}
           style={{ height }} aria-hidden="true">
        <line x1="0" y1={height / 2} x2={width} y2={height / 2}
              stroke="#1F2227" strokeDasharray="3 4" strokeWidth="1" />
      </svg>
    );
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
         className={`block w-full ${className}`} style={{ height }} aria-hidden="true">
      <defs>
        <linearGradient id={`sg-${gradId}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"  stopColor={fillStop} />
          <stop offset="100%" stopColor="transparent" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#sg-${gradId})`} />
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.5"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
