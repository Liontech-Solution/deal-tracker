import { useLayoutEffect, useRef, useState } from 'react';

import type { PricePoint } from '../api/types';
import { marcasRedondas } from '../lib/chart-scale';
import { eur, parseMoney } from '../lib/format';

const H = 200;
/** Por debajo de esto el eje no cabe; ningún viewport real baja tanto, pero el cálculo no se rompe. */
const W_MIN = 260;
// `l` reserva sitio para las etiquetas del eje Y ('199,99 €' es lo más ancho que cabe esperar);
// `b`, para las fechas del eje X. Antes eran 8 px a cada lado y el hueco de abajo se reservaba sin
// usarse: el eje no se dibujaba (#236).
const PAD = { l: 54, r: 10, t: 16, b: 24 };

const fmtDate = new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short' });

export function PriceHistoryChart({ history }: { history: PricePoint[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  /*
   * El lienzo mide lo que mide el hueco, en PÍXELES REALES.
   *
   * Antes el `viewBox` era fijo (560 de ancho) y el SVG se servía con `width: 100%`, así que el
   * navegador lo escalaba — y con él, el texto. Medido en este mismo arreglo: las etiquetas del eje
   * salían a 9,6 px en escritorio y a **5,7 px en un viewport de 390**, ilegibles justo donde más
   * falta hacen, porque en móvil no hay hover (#236). Midiendo el contenedor, una unidad del
   * `viewBox` es un píxel de pantalla y `fontSize={10.5}` son 10,5 px en cualquier viewport.
   *
   * De paso deja de aplastarse: con el lienzo fijo, los 200 de alto se quedaban en 109 px reales en
   * móvil.
   */
  const [W, setW] = useState(560);
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const medir = (ancho: number) => setW(Math.max(W_MIN, Math.round(ancho)));
    medir(el.getBoundingClientRect().width);
    // `ResizeObserver` y no un listener de `resize`: el hueco cambia también cuando lo hace el
    // layout de la ficha (galería, chips de talla), no solo cuando se redimensiona la ventana.
    const ro = new ResizeObserver(([e]) => medir(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const points = history
    .map((h) => ({ price: parseMoney(h.price), list: parseMoney(h.listPrice), t: h.scrapedAt }))
    .filter((p): p is { price: number; list: number | null; t: string } => p.price !== null);

  // El contenedor medido se monta SIEMPRE, también sin histórico: si el estado vacío devolviera otro
  // elemento, el `ResizeObserver` se quedaría observando un nodo ya desmontado en cuanto llegaran
  // los datos, y el lienzo se quedaría con el ancho de arranque.
  if (points.length < 2) {
    return (
      <div ref={boxRef}>
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
  const maxVal = Math.max(...prices);

  // Línea de PVP: unimos los list_price conocidos (discontinua).
  const listCoords = points
    .map((p, i) => (p.list !== null ? { x: x(i), y: y(p.list) } : null))
    .filter((c): c is { x: number; y: number } => c !== null);
  const listLine = listCoords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');

  const marcas = marcasRedondas(ymin, ymax);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };

  const hv = hover !== null ? coords[hover] : null;

  const desde = fmtDate.format(new Date(points[0].t));
  const hasta = fmtDate.format(new Date(points[points.length - 1].t));

  return (
    <div ref={boxRef}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto', display: 'block', touchAction: 'none' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        /*
         * Con el eje puesto, exponer el rango aquí es casi gratis — y es lo único que un lector de
         * pantalla obtiene: antes el `aria-label` era «Gráfica de evolución del precio» y ni un
         * número (#236).
         */
        aria-label={
          `Evolución del precio entre el ${desde} y el ${hasta}, en ${points.length} mediciones. ` +
          `El precio se movió entre ${eur(minVal)} y ${eur(maxVal)}. ` +
          `Mínimo histórico: ${eur(minVal)}. El eje vertical no arranca en cero.`
        }
      >
        <defs>
          <linearGradient id="dt-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.18" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/*
          Eje Y. Las líneas de referencia van SÓLIDAS y un tono por encima de la superficie: una
          rejilla discontinua compite con la línea de PVP, que sí es discontinua porque significa
          otra cosa.
        */}
        {marcas.map((v) => (
          <g key={v}>
            <line
              x1={PAD.l}
              y1={y(v)}
              x2={W - PAD.r}
              y2={y(v)}
              stroke="var(--border)"
              strokeWidth={1}
            />
            <text
              x={PAD.l - 8}
              y={y(v) + 3.5}
              textAnchor="end"
              fontSize={10.5}
              fill="var(--text-faint)"
              style={{ fontVariantNumeric: 'tabular-nums' }}
            >
              {eur(v)}
            </text>
          </g>
        ))}

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

        {/* Eje X: la primera y la última medición, ancladas para que no se salgan del lienzo. */}
        <text
          x={PAD.l}
          y={H - 6}
          textAnchor="start"
          fontSize={10.5}
          fill="var(--text-faint)"
          style={{ fontVariantNumeric: 'tabular-nums' }}
        >
          {desde}
        </text>
        <text
          x={W - PAD.r}
          y={H - 6}
          textAnchor="end"
          fontSize={10.5}
          fill="var(--text-faint)"
          style={{ fontVariantNumeric: 'tabular-nums' }}
        >
          {hasta}
        </text>

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

      {/*
        El aviso de la escala recortada, y la mitad que de verdad cierra la #236.
        Recortar el eje es lo correcto —arrancar en cero aplasta las bajadas reales y las vuelve
        invisibles—, pero recortarlo Y NO DECIRLO es lo que hace que una oscilación de dos euros se
        dibuje igual de dramática que un desplome de 40 € a 12 €. Va en HTML y no dentro del SVG a
        propósito: el lienzo se sirve con `width: 100%`, así que un texto de dentro se encogería con
        todo lo demás y en móvil quedaría ilegible.
      */}
      <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 6 }}>
        El eje no arranca en cero: la escala se ajusta al recorrido de esta prenda
        {` (${eur(minVal)} – ${eur(maxVal)})`}.
      </div>
    </div>
  );
}
