-- Serie temporal de lo que tarda el vigía, por tienda y por capa (#111).
--
-- El vigía contesta bien «¿nos dejan entrar?» y tiraba la otra señal que ya tenía medida: cuánto
-- ha tardado en contestarla. Medido el 02/08/2026 sobre Hipercor, la misma tienda a la misma hora,
-- el sondeo de hojas tardó **24 min 28 s desde el pod y 2 min 04 s desde fuera** (×11,8) con
-- veredicto verde en los dos casos: la tienda seguía regulándonos el paso tras el bloqueo de #107
-- y desde el informe eso era invisible.
--
-- Importa porque dimensionar el `activeDeadlineSeconds` de una tienda depende del ritmo al que nos
-- dejan entrar, y sin esta tabla eso solo se descubre cuando una pasada real muere por deadline —
-- que con ingesta atómica significa que el catálogo no se puebla, y si nunca cabe no se puebla
-- nunca. Con la serie, la sorpresa se convierte en tendencia.
--
-- Propiedad del **scraper**, como `retailer/product/variant/price_history/scrape_run`. El servicio
-- web no la lee hoy; está en el espejo Drizzle solo para que el contrato no se bifurque.
--
-- `retailer_slug` TEXT y NO una FK a `retailer(id)`, que es la decisión menos obvia de aquí: el
-- vigía recorre `registry.available_slugs()` y sondea tiendas que pueden **no tener fila en
-- `retailer`** todavía —esa fila la crea la primera ingesta, y #93 lo comprobó con Hipercor, que no
-- la tenía en `dev`—. Con FK, la tienda que más interesa vigilar (la que aún no ha ingerido nunca)
-- sería justo la única que no se puede medir.
--
-- Formato largo, una fila por capa, en vez de columnas `segundos_hojas`/`segundos_parseo`: una
-- tercera capa futura entra sin migración.
--
-- `unidades` es lo que hace comparable la medida entre semanas: un absoluto por tienda envejece mal
-- porque los catálogos crecen, así que lo que se compara es **segundos por unidad** (por hoja
-- sondeada, por producto parseado). Se guarda aunque la capa reventase a mitad —las unidades serán
-- las que llegó a cubrir—, porque «murió tras 12 hojas en 8 min» es un dato; que una muestra sirva
-- o no de línea base lo decide `vigia_historial.py` comparándola con la cobertura habitual de esa
-- misma tienda, y no con un mínimo absoluto: Cacles publica una sola hoja.
--
-- Sin poda: 7 tiendas x 2 capas x semanal son ~730 filas al año. No merece un job de retención.
CREATE TABLE vigia_run (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    retailer_slug TEXT           NOT NULL,
    capa          TEXT           NOT NULL,  -- hojas | parseo
    ran_at        TIMESTAMPTZ    NOT NULL DEFAULT now(),
    segundos      NUMERIC(10, 3) NOT NULL,
    unidades      INTEGER        NOT NULL
);

-- La única consulta que existe: las últimas N muestras de una tienda y capa, más recientes primero.
CREATE INDEX ix_vigia_run_slug_capa ON vigia_run (retailer_slug, capa, ran_at DESC);
