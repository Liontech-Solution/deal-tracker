import { useEffect, useState } from 'react';

import { useCreateInterest } from '../api/hooks';
import type { CompareBase, CreateInterestInput } from '../api/types';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthProvider';
import { BellIcon, CheckIcon, CloseIcon } from './icons';
import { useToast } from './Toast';

/** Objetivo del seguimiento que se abre desde la ficha de producto. */
export interface FollowTarget {
  productId: number;
  productName: string;
  /** Variante seleccionada (si la hay): permite acotar el aviso a esa talla/color. */
  variantId?: number;
  variantLabel?: string | null;
  /**
   * Las OTRAS medidas que la tienda publica bajo la misma etiqueta de talla (#331). Cuando H&M
   * vende '0-1 meses (44 cm)' y '0-1 meses (50 cm)', el interés se guarda por la talla canónica
   * —'0-1 meses'— así que cubre las dos, y el usuario tiene derecho a saberlo ANTES de seguirla.
   *
   * Decirlo es mejor que fingir una precisión que no hay: la alternativa era guardar la variante
   * exacta, y eso rompe que el aviso case la misma talla en otra tienda (#223).
   */
  otrasMedidas?: string[];
}

interface FollowModalProps {
  open: boolean;
  onClose: () => void;
  target: FollowTarget | null;
}

const WINDOW_OPTIONS = [7, 14, 30, 60, 90];

/**
 * Modal "Configurar seguimiento": crea un `interest` para el producto (opcionalmente acotado a la
 * variante seleccionada) con la regla de aviso. Mapea 1:1 a `CreateInterestDto` del backend.
 */
export function FollowModal({ open, onClose, target }: FollowModalProps) {
  const toast = useToast();
  const { authenticated } = useAuth();
  const createInterest = useCreateInterest();

  const hasVariant = Boolean(target?.variantId);
  const [onlyVariant, setOnlyVariant] = useState(hasVariant);
  const [minDiscountPct, setMinDiscountPct] = useState(20);
  const [compareBase, setCompareBase] = useState<CompareBase>('recent_min');
  const [windowDays, setWindowDays] = useState(30);

  // Reinicia el formulario cada vez que se abre con un objetivo nuevo.
  useEffect(() => {
    if (open) {
      setOnlyVariant(Boolean(target?.variantId));
      setMinDiscountPct(20);
      setCompareBase('recent_min');
      setWindowDays(30);
    }
  }, [open, target]);

  // Cierra con Escape.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open || !target) return null;

  const submit = () => {
    const input: CreateInterestInput = {
      productId: target.productId,
      ...(onlyVariant && target.variantId ? { variantId: target.variantId } : {}),
      minDiscountPct,
      compareBase,
      windowDays,
    };
    createInterest.mutate(input, {
      onSuccess: () => {
        toast('Seguimiento creado · te avisaremos por Telegram');
        onClose();
      },
      onError: (err) => {
        if (err instanceof ApiError && err.status === 401) {
          toast('Tu sesión ha caducado, vuelve a iniciar sesión');
        } else {
          toast(err instanceof Error ? err.message : 'No se pudo crear el seguimiento');
        }
      },
    });
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Configurar seguimiento"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 80,
        background: 'color-mix(in srgb, var(--ink-900) 42%, transparent)',
        backdropFilter: 'blur(3px)',
        display: 'grid',
        placeItems: 'end center',
        padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="dt-fade"
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--r-lg)',
          boxShadow: 'var(--shadow-3)',
          width: '100%',
          maxWidth: 460,
          padding: 22,
          marginBottom: '6vh',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ width: 36, height: 36, borderRadius: 11, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center', flex: 'none' }}>
              <BellIcon size={18} />
            </span>
            <div>
              <div className="serif" style={{ fontSize: 19, fontWeight: 700, lineHeight: 1.1 }}>Configurar seguimiento</div>
              <div style={{ fontSize: 13, color: 'var(--text-faint)' }}>{target.productName}</div>
            </div>
          </div>
          <button onClick={onClose} aria-label="Cerrar" className="btn-ghost" style={{ width: 36, height: 36, borderRadius: 'var(--r-pill)', display: 'grid', placeItems: 'center', flex: 'none' }}>
            <CloseIcon size={17} />
          </button>
        </div>

        {!authenticated && (
          <div style={{ margin: '10px 0 2px', padding: '10px 12px', borderRadius: 'var(--r-sm)', background: 'var(--surface-2)', color: 'var(--text-muted)', fontSize: 13 }}>
            Inicia sesión para guardar este seguimiento.
          </div>
        )}

        {/* Alcance: variante concreta vs todo el producto */}
        {hasVariant && (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontWeight: 800, fontSize: 13.5, marginBottom: 8 }}>¿Qué seguimos?</div>
            <div style={{ display: 'grid', gap: 8 }}>
              <ScopeOption
                selected={onlyVariant}
                onClick={() => setOnlyVariant(true)}
                title={`Solo esta variante`}
                sub={target.variantLabel ?? 'Talla/color seleccionados'}
              />
              {onlyVariant && target.otrasMedidas?.length ? (
                <div
                  style={{
                    fontSize: 12.5,
                    color: 'var(--text-faint)',
                    lineHeight: 1.45,
                    padding: '0 2px',
                  }}
                >
                  Esta tienda publica {target.otrasMedidas.length + 1} medidas con esta misma
                  talla ({target.otrasMedidas.join(', ')} y la elegida). Te avisaremos de todas.
                </div>
              ) : null}
              <ScopeOption
                selected={!onlyVariant}
                onClick={() => setOnlyVariant(false)}
                title="Cualquier variante del producto"
                sub="Todas las tallas y colores"
              />
            </div>
          </div>
        )}

        {/* Regla: descuento mínimo */}
        <div style={{ marginTop: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
            <span style={{ fontWeight: 800, fontSize: 13.5 }}>Avísame con al menos</span>
            <span style={{ fontWeight: 800, fontSize: 15, color: 'var(--accent)' }}>−{minDiscountPct}%</span>
          </div>
          <input
            type="range"
            min={5}
            max={70}
            step={5}
            value={minDiscountPct}
            onChange={(e) => setMinDiscountPct(Number(e.target.value))}
            style={{ width: '100%', accentColor: 'var(--accent)' }}
            aria-label="Descuento mínimo"
          />
        </div>

        {/* Regla: base de comparación */}
        <div style={{ marginTop: 16 }}>
          <div style={{ fontWeight: 800, fontSize: 13.5, marginBottom: 8 }}>Comparado contra</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <ScopeOption
              selected={compareBase === 'recent_min'}
              onClick={() => setCompareBase('recent_min')}
              title="Mínimo reciente"
              sub="Descuento real"
            />
            <ScopeOption
              selected={compareBase === 'list_price'}
              onClick={() => setCompareBase('list_price')}
              title="PVP (precio de lista)"
              sub="Sobre el precio oficial"
            />
          </div>
        </div>

        {/* Regla: ventana temporal */}
        <div style={{ marginTop: 16 }}>
          <div style={{ fontWeight: 800, fontSize: 13.5, marginBottom: 8 }}>Ventana de comparación</div>
          <select
            className="select"
            value={windowDays}
            onChange={(e) => setWindowDays(Number(e.target.value))}
            style={{ width: '100%' }}
            aria-label="Ventana en días"
          >
            {WINDOW_OPTIONS.map((d) => (
              <option key={d} value={d}>
                Últimos {d} días
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: 22 }}>
          <button onClick={onClose} className="btn btn-secondary" style={{ flex: 'none', padding: '13px 18px', fontSize: 14.5 }}>
            Cancelar
          </button>
          <button
            onClick={submit}
            disabled={!authenticated || createInterest.isPending}
            className="btn btn-primary"
            style={{ flex: 1, padding: 13, fontSize: 15, opacity: !authenticated || createInterest.isPending ? 0.6 : 1 }}
          >
            <CheckIcon size={17} sw={3} />
            {createInterest.isPending ? 'Creando…' : 'Crear aviso'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ScopeOption({
  selected,
  onClick,
  title,
  sub,
}: {
  selected: boolean;
  onClick: () => void;
  title: string;
  sub: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={selected}
      style={{
        textAlign: 'left',
        cursor: 'pointer',
        borderRadius: 'var(--r-sm)',
        padding: '11px 13px',
        border: '1.5px solid ' + (selected ? 'var(--accent)' : 'var(--border)'),
        background: selected ? 'var(--accent-soft)' : 'var(--surface)',
      }}
    >
      <div style={{ fontWeight: 800, fontSize: 13.5, color: selected ? 'var(--accent)' : 'var(--text)' }}>{title}</div>
      <div style={{ fontSize: 12, color: 'var(--text-faint)', marginTop: 1 }}>{sub}</div>
    </button>
  );
}
