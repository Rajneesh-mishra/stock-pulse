// Build SVG path strings from a series of close prices.
// Produces both the area (filled under the line) and the line itself.
// Uses Catmull–Rom → cubic Bézier for a smooth curve.

export type SparkPath = { line: string; area: string; dir: 'up' | 'dn' | 'flat' };

export function sparklinePath(values: number[], w: number, h: number, pad = 2): SparkPath {
  if (!values || values.length < 2) return { line: '', area: '', dir: 'flat' };
  const n = values.length;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = (max - min) || 1;
  const stepX = (w - pad * 2) / (n - 1);
  const pts: Array<[number, number]> = values.map((v, i) => {
    const x = pad + i * stepX;
    const y = pad + (h - pad * 2) * (1 - (v - min) / range);
    return [x, y];
  });

  // smooth Catmull–Rom to bezier
  const line = catmullRom2bezier(pts);
  const area = `${line} L ${pts[n - 1][0].toFixed(2)},${h} L ${pts[0][0].toFixed(2)},${h} Z`;

  const first = values[0];
  const last = values[n - 1];
  const dir: SparkPath['dir'] =
    last > first ? 'up' : last < first ? 'dn' : 'flat';
  return { line, area, dir };
}

function catmullRom2bezier(points: Array<[number, number]>): string {
  if (points.length < 2) return '';
  const segs: string[] = [`M ${points[0][0].toFixed(2)},${points[0][1].toFixed(2)}`];
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] || points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;
    const cp1x = p1[0] + (p2[0] - p0[0]) / 6;
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6;
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6;
    segs.push(
      `C ${cp1x.toFixed(2)},${cp1y.toFixed(2)} ` +
      `${cp2x.toFixed(2)},${cp2y.toFixed(2)} ` +
      `${p2[0].toFixed(2)},${p2[1].toFixed(2)}`,
    );
  }
  return segs.join(' ');
}
