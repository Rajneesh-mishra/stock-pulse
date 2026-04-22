import { useEffect, useState } from 'react';
import { detectMode } from '../lib/api';
import type { Mode } from '../types';

export function useMode(): Mode {
  const [mode, setMode] = useState<Mode>('connecting');
  useEffect(() => {
    let alive = true;
    detectMode().then(m => { if (alive) setMode(m); });
    return () => { alive = false; };
  }, []);
  return mode;
}
