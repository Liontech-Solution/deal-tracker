import { useRef, useState } from 'react';

import type { PricePoint } from '../api/types';
import { eur, parseMoney } from '../lib/format';

const W = 560;
const H = 200;
const PAD = { l: 8, r: 8, t: 16, b: 22 };

const fmtDate = new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short' });

export function PriceHistoryChart({ history }: { history: PricePoint[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const points = history
    .map((h) => ({ price: parseMoney(h.price), list: parseMoney(h.listPrice), t: h.scrapedAt }))
    .filter((p): p is { price: number; list: number | null; t: string } => p.price !== null);

  if (points.length < 2) {
    return (
      <div
        style={{
          height: 160,
          display: 'grid',
          placeItems: 'center',
          color: 'var(--text-faint)',
          fontSize: 13.5,
          border: '1px dashed var(--border)',
          borderRadius: 'var(--r-md)',
        }}
      >
        Aún no hay suficiente histórico para dibujar la evolución.
      </div>
    );
  }

  const prices = points.map((p) => p.price);
  const lists = points.map((p) => p.list).filter((v): v is number => v !== null);
  const ymax = Math.max(...prices, ...lists) * 1.04;
  const ymin = Math.min(...prices) * 0.94;

  const x = (i: number) => PAD.l + (i / (points.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - ymin) / (ymax - ymin || 1)) * (H - PAD.t - PAD.b);

  const coords = points.map((p, i) => ({ x: x(i), y: y(p.price), ...p, i }));
  const line = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
  const area = `M${coords[0].x.toFixed(1)},${H - PAD.b} L${coords
    .map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`)
    .join(' L')} L${coords[coords.length - 1].x.toFixed(1)},${H - PAD.b} Z`;

  const minVal = Math.min(...prices);
  const minI = prices.indexOf(minVal);

  // Línea de PVP: unimos los list_price conocidos (discontinua).
  const listCoords = points
    .map((p, i) => (p.list !== null ? { x: x(i), y: y(p.list) } : null))
    .filter((c): c is { x: number; y: number } => c !== null);
  const listLine = listCoords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };

  const hv = hover !== null ? coords[hover] : null;

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: '100%', height: 'auto', display: 'block', touchAction: 'none' }}
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
      role="img"
      aria-label="Gráfica de evolución del precio"
    >
      <defs>
        <linearGradient id="dt-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.18" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>

      <path d={area} fill="url(#dt-area)" />
      {listCoords.length >= 2 && (
        <polyline
          points={listLine}
          fill="none"
          stroke="var(--text-faint)"
          strokeWidth={1.6}
          strokeDasharray="5 5"
        />
      )}
      <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth={2.4} strokeLinejoin="round" />

      {/* mínimo histórico */}
      <circle cx={x(minI)} cy={y(minVal)} r={5} fill="var(--good)" stroke="var(--surface)" strokeWidth={2} />

      {/* hover */}
      {hv && (
        <g>
          <line x1={hv.x} y1={PAD.t} x2={hv.x} y2={H - PAD.b} stroke="var(--border-strong)" strokeWidth={1} />
          <circle cx={hv.x} cy={hv.y} r={5} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
          <g transform={`translate(${Math.min(Math.max(hv.x, 46), W - 46)}, 12)`}>
            <rect x={-44} y={-2} width={88} height={34} rx={8} fill="var(--ink-900)" />
            <text x={0} y={11} textAnchor="middle" fontSize={11} fontWeight={800} fill="#fff">
              {eur(hv.price)}
            </text>
            <text x={0} y={24} textAnchor="middle" fontSize={9.5} fill="rgba(255,255,255,.7)">
              {fmtDate.format(new Date(hv.t))}
            </text>
          </g>
        </g>
      )}
    </svg>
  );
}
