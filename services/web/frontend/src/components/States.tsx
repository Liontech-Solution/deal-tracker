import type { ReactNode } from 'react';

import { AlertIcon, SearchIcon } from './icons';

export function ProductGridSkeleton({ count = 8 }: { count?: number }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(200px,1fr))', gap: 16 }}>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--r-lg)',
            overflow: 'hidden',
          }}
        >
          <div className="dt-skel" style={{ aspectRatio: '1', borderRadius: 0 }} />
          <div style={{ padding: 14 }}>
            <div className="dt-skel" style={{ height: 11, width: '40%', marginBottom: 10 }} />
            <div className="dt-skel" style={{ height: 15, width: '85%', marginBottom: 8 }} />
            <div className="dt-skel" style={{ height: 22, width: '55%' }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function Panel({ children, dashed = false }: { children: ReactNode; dashed?: boolean }) {
  return (
    <div
      style={{
        textAlign: 'center',
        padding: '70px 20px',
        background: 'var(--surface)',
        border: dashed ? '1px dashed var(--border-strong)' : '1px solid var(--border)',
        borderRadius: 'var(--r-lg)',
      }}
    >
      {children}
    </div>
  );
}

export function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <Panel>
      <div
        style={{
          width: 64,
          height: 64,
          borderRadius: 20,
          background: 'var(--warn-soft)',
          color: 'var(--warn-text)',
          display: 'grid',
          placeItems: 'center',
          margin: '0 auto 18px',
        }}
      >
        <AlertIcon size={30} />
      </div>
      <div className="serif" style={{ fontSize: 23, marginBottom: 6 }}>
        No hemos podido cargar el catálogo
      </div>
      <p style={{ color: 'var(--text-muted)', maxWidth: 380, margin: '0 auto 20px' }}>
        Puede ser un problema temporal de conexión con las tiendas. Inténtalo de nuevo en un momento.
      </p>
      <button className="btn btn-primary" style={{ padding: '12px 22px' }} onClick={onRetry}>
        Reintentar
      </button>
    </Panel>
  );
}

export function EmptyState({ onClear }: { onClear: () => void }) {
  return (
    <Panel dashed>
      <div
        style={{
          width: 64,
          height: 64,
          borderRadius: 20,
          background: 'var(--surface-2)',
          color: 'var(--text-faint)',
          display: 'grid',
          placeItems: 'center',
          margin: '0 auto 18px',
        }}
      >
        <SearchIcon size={30} />
      </div>
      <div className="serif" style={{ fontSize: 23, marginBottom: 6 }}>
        Sin resultados
      </div>
      <p style={{ color: 'var(--text-muted)', maxWidth: 380, margin: '0 auto 20px' }}>
        No hay prendas que coincidan con estos filtros. Prueba a quitar alguno.
      </p>
      <button className="btn btn-secondary" style={{ padding: '12px 22px' }} onClick={onClear}>
        Limpiar filtros
      </button>
    </Panel>
  );
}
