import { useState } from 'react';

import { sectionBg, stripeBg } from '../lib/section';

/**
 * Foto de producto servida directamente desde el CDN de la tienda (hotlink): sin proxy ni
 * almacenamiento propio, que en un cluster de Raspberry Pi no compensa. Si no hay URL —Sfera
 * todavía no la da, y las fichas de Zara la estrenan según se les vuelve a pedir el detalle— o
 * la carga falla, se cae al placeholder de rayas del diseño, que es lo que se veía antes.
 */
export function ProductImage({
  src,
  alt,
  section,
  width,
  aspectRatio = '1',
}: {
  src: string | null;
  alt: string;
  section: string | null;
  /** Ancho que se le pide al CDN: la foto completa pesa ~124 KB y a 563 px baja a ~10 KB. */
  width: number;
  aspectRatio?: string;
}) {
  const [failed, setFailed] = useState(false);

  if (!src || failed) {
    return (
      <div style={{ aspectRatio, background: stripeBg(sectionBg(section)), display: 'grid', placeItems: 'center' }}>
        <span
          style={{
            fontSize: 11,
            color: 'var(--ink-500)',
            background: 'var(--surface)',
            padding: '5px 11px',
            borderRadius: 99,
            border: '1px solid var(--border)',
            fontWeight: 700,
          }}
        >
          SIN FOTO
        </span>
      </div>
    );
  }

  return (
    <img
      // `&` y no `?`: la URL del CDN ya trae su propio query (`?ts=`).
      src={`${src}&w=${width}`}
      alt={alt}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      style={{ display: 'block', width: '100%', aspectRatio, objectFit: 'cover', background: 'var(--sand-100)' }}
    />
  );
}
