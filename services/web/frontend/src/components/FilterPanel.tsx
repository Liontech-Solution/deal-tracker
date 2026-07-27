import type { Facets } from '../api/types';
import { capitalize } from '../lib/format';
import { colorHex } from '../lib/colors';

export interface CatalogFilters {
  gender: string;
  section: string;
  category: string;
  size: string;
  color: string;
  retailer: string;
  inStock: boolean;
  onlyDeals: boolean;
}

interface Props {
  facets: Facets | undefined;
  value: CatalogFilters;
  onChange: (patch: Partial<CatalogFilters>) => void;
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ padding: '14px 0', borderTop: '1px solid var(--border)' }}>
      <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 10, color: 'var(--text)' }}>{label}</div>
      {children}
    </div>
  );
}

function Chip({ label, selected, onClick, dot }: { label: string; selected: boolean; onClick: () => void; dot?: string | null }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={selected}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7,
        border: '1px solid ' + (selected ? 'transparent' : 'var(--border)'),
        background: selected ? 'var(--accent-soft)' : 'var(--surface)',
        color: selected ? 'var(--accent)' : 'var(--text-muted)',
        borderRadius: 'var(--r-pill)',
        padding: dot ? '6px 12px 6px 8px' : '6px 12px',
        fontSize: 13,
        fontWeight: 700,
        cursor: 'pointer',
      }}
    >
      {dot && <span style={{ width: 12, height: 12, borderRadius: '50%', background: dot, border: '1px solid var(--border-strong)', flex: 'none' }} />}
      {label}
    </button>
  );
}

function Switch({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, cursor: 'pointer' }}>
      <span style={{ fontSize: 14, fontWeight: 700 }}>{label}</span>
      <button
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        style={{
          width: 46,
          height: 28,
          borderRadius: 999,
          border: 'none',
          cursor: 'pointer',
          background: checked ? 'var(--accent)' : 'var(--border-strong)',
          position: 'relative',
          transition: 'background .15s ease',
          flex: 'none',
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: 3,
            left: checked ? 21 : 3,
            width: 22,
            height: 22,
            borderRadius: '50%',
            background: '#fff',
            transition: 'left .15s ease',
            boxShadow: 'var(--shadow-1)',
          }}
        />
      </button>
    </label>
  );
}

export function FilterPanel({ facets, value, onChange }: Props) {
  const toggle = (key: keyof CatalogFilters, v: string) => onChange({ [key]: value[key] === v ? '' : v });

  return (
    <div>
      {/* El género vivía en una barra de pestañas de la cabecera, pegada a la de sección y
          confundiéndose con ella. Es un filtro como talla o color, así que va donde están todos. */}
      {facets?.genders.length ? (
        <Group label="Para">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {facets.genders.map((g) => (
              <Chip key={g} label={capitalize(g)} selected={value.gender === g} onClick={() => toggle('gender', g)} />
            ))}
          </div>
        </Group>
      ) : null}

      {facets?.categories.length ? (
        <Group label="Categoría">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {facets.categories.map((c) => (
              <Chip key={c} label={capitalize(c)} selected={value.category === c} onClick={() => toggle('category', c)} />
            ))}
          </div>
        </Group>
      ) : null}

      {facets?.sizes.length ? (
        <Group label="Talla">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {facets.sizes.map((s) => (
              <Chip key={s} label={s} selected={value.size === s} onClick={() => toggle('size', s)} />
            ))}
          </div>
        </Group>
      ) : null}

      {facets?.colors.length ? (
        <Group label="Color">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {facets.colors.map((c) => (
              <Chip key={c} label={capitalize(c)} selected={value.color === c} onClick={() => toggle('color', c)} dot={colorHex(c)} />
            ))}
          </div>
        </Group>
      ) : null}

      {facets?.retailers.length ? (
        <Group label="Tienda">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {facets.retailers.map((r) => (
              <Chip key={r.slug} label={r.name} selected={value.retailer === r.slug} onClick={() => toggle('retailer', r.slug)} />
            ))}
          </div>
        </Group>
      ) : null}

      <Group label="Ofertas">
        <Switch
          label="Solo ofertas reales"
          checked={value.onlyDeals}
          onChange={(v) => onChange({ onlyDeals: v })}
        />
        <div style={{ fontSize: 12.5, color: 'var(--text-faint)', marginTop: 8, lineHeight: 1.45 }}>
          Deja solo lo que ha bajado de verdad respecto a su mínimo reciente. Apagado, el catálogo
          se ve entero con las ofertas primero.
        </div>
      </Group>

      <Group label="Disponibilidad">
        <Switch
          label="Solo en stock"
          checked={value.inStock}
          onChange={(v) => onChange({ inStock: v })}
        />
      </Group>
    </div>
  );
}
