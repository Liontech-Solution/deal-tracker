import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import {
  makeApp,
  makeSql,
  resetSchema,
  seedCatalog,
  seedUser,
  SEED_IMAGE_URL,
  TEST_DB,
} from './helpers';
import type { SeedIds } from './helpers';

describe.skipIf(!TEST_DB)('intereses (e2e)', () => {
  let sql: postgres.Sql;
  let ids: SeedIds;

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    ids = await seedCatalog(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  it('sin token -> 401', async () => {
    const app = await makeApp(); // sin override: guard real de Keycloak
    try {
      await request(app.getHttpServer()).get('/api/interests').expect(401);
    } finally {
      await app.close();
    }
  });

  it('CRUD del interés propio (crear, listar, borrar)', async () => {
    const user = await seedUser(sql);
    const app = await makeApp(user);
    try {
      // crear
      const created = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ gender: 'niña', section: 'zapateria', minDiscountPct: 30, compareBase: 'list_price' })
        .expect(201);
      expect(created.body.gender).toBe('niña');
      expect(created.body.minDiscountPct).toBe('30.00');
      expect(created.body.compareBase).toBe('list_price');
      const id = created.body.id;

      // listar
      const listed = await request(app.getHttpServer()).get('/api/interests').expect(200);
      expect(listed.body).toHaveLength(1);
      expect(listed.body[0].id).toBe(id);

      // borrar
      await request(app.getHttpServer()).delete(`/api/interests/${id}`).expect(204);
      await request(app.getHttpServer())
        .get('/api/interests')
        .expect(200)
        .expect((r) => expect(r.body).toHaveLength(0));
    } finally {
      await app.close();
    }
  });

  it('enriquece la lista con nombre de producto/variante/tienda al apuntar a un objetivo', async () => {
    const user = await seedUser(sql, 'kc-sub-enrich');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .post('/api/interests')
        .send({ productId: ids.productId, variantId: ids.variantId })
        .expect(201);

      const listed = await request(app.getHttpServer()).get('/api/interests').expect(200);
      expect(listed.body).toHaveLength(1);
      const view = listed.body[0];
      expect(view.productName).toBe('Botas niña');
      expect(view.retailerName).toBe('Zara');
      expect(view.variantLabel).toBe('Talla 24 · rojo');
      // #302: con qué enseñar la prenda. El seed no tiene galería, así que la foto es la principal
      // del producto — el respaldo, que no es un caso raro: la galería la estrenan las fichas según
      // se les vuelve a pedir el detalle.
      expect(view.targetProductId).toBe(Number(ids.productId));
      expect(view.imageUrl).toBe(SEED_IMAGE_URL);
      expect(view.productSection).toBe('zapateria');
    } finally {
      await app.close();
    }
  });

  it('la foto es la DEL COLOR seguido cuando la galería la tiene (#302)', async () => {
    // Dos colores del mismo producto con foto propia: la tarjeta tiene que enseñar la del color de
    // la variante seguida, no «la primera». Es el mismo criterio que la tarjeta del catálogo, y sin
    // él un interés de la bota roja podría ilustrarse con la foto de la azul.
    const [azul] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${ids.productId}, 'ZARA-1-24-azul', '24', 'azul', 'SKU24AZ')
      RETURNING id`;
    await sql`
      INSERT INTO product_image (product_id, color, position, url)
      VALUES (${ids.productId}, 'rojo', 0, 'https://static.example/p/ZARA-1-rojo.jpg'),
             (${ids.productId}, 'azul', 0, 'https://static.example/p/ZARA-1-azul.jpg')`;

    const user = await seedUser(sql, 'kc-sub-color-foto');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .post('/api/interests')
        .send({ variantId: azul.id })
        .expect(201);

      const listed = await request(app.getHttpServer()).get('/api/interests').expect(200);
      const view = listed.body[0];
      expect(view.imageUrl).toBe('https://static.example/p/ZARA-1-azul.jpg');
      // Y el enlace sale por la variante: este interés NO trae `productId`, que es su alcance.
      expect(view.productId).toBeNull();
      expect(view.targetProductId).toBe(Number(ids.productId));
    } finally {
      await app.close();
      await sql`DELETE FROM product_image WHERE product_id = ${ids.productId}`;
      await sql`DELETE FROM variant WHERE id = ${azul.id}`;
    }
  });

  it('la etiqueta de la variante lleva la talla CANÓNICA, no la de la tienda (#223)', async () => {
    // El seed usa la talla '24', que es su propia canónica: con ella este defecto es invisible.
    // Hace falta una talla con el sufijo de unidad que publican Zara y compañía, que es donde
    // `size_canon` sí cambia el texto — y donde `/interests` devolvía el crudo mientras las
    // facetas, los filtros y el chip guardado decían otra cosa.
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${ids.productId}, 'ZARA-1-2anios-rosa', '2 años (92 cm)', 'Rosa / Blanco', 'SKU2A')
      RETURNING id`;

    const user = await seedUser(sql, 'kc-sub-label-canon');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer()).post('/api/interests').send({ variantId: v.id }).expect(201);

      const listed = await request(app.getHttpServer()).get('/api/interests').expect(200);
      expect(listed.body).toHaveLength(1);
      const view = listed.body[0];
      // `Number(...)`: los ids de `bigint` los devuelve postgres.js como string.
      expect(view.variantId).toBe(Number(v.id));
      // El color va CRUDO a propósito: `color_canon` devuelve NULL para lo que no reconoce (#51),
      // así que canonizarlo aquí borraría el color en vez de normalizarlo.
      expect(view.variantLabel).toBe('Talla 2 años · Rosa / Blanco');
    } finally {
      await app.close();
    }
  });

  it('un interés por filtros (sin objetivo) trae los nombres a null', async () => {
    const user = await seedUser(sql, 'kc-sub-filter');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .post('/api/interests')
        .send({ gender: 'niña', section: 'zapateria' })
        .expect(201);
      const listed = await request(app.getHttpServer()).get('/api/interests').expect(200);
      const view = listed.body[0];
      expect(view.productName).toBeNull();
      expect(view.variantLabel).toBeNull();
      expect(view.retailerName).toBeNull();
      // #302: no apunta a ninguna prenda, así que no hay foto ni ficha a la que enlazar. Es el caso
      // que la tarjeta tiene que seguir aguantando: 'toda la ropa de niña rebajada un 30 %' se
      // enseña con el resumen de sus filtros y nada más.
      expect(view.targetProductId).toBeNull();
      expect(view.imageUrl).toBeNull();
      expect(view.productSection).toBeNull();
    } finally {
      await app.close();
    }
  });

  it('guarda la talla en canónico, venga como venga (#43)', async () => {
    // El chip del filtro ya manda la canónica, pero un alta por API con el texto crudo de la tienda
    // tiene que seguir a la misma prenda: si se guardara '26 (16,3 cm)', ese interés solo casaría
    // con Zara y nunca con el mismo pie en otra tienda.
    const user = await seedUser(sql, 'kc-talla-canon');
    const app = await makeApp(user);
    try {
      for (const [entrada, esperada] of [
        ['26 (16,3 cm)', '26'],
        ['11-12', '11-12 años'],
        ['26', '26'],
        // Rangos de número de pie (#64): no se etiquetan como edad, y el separador se normaliza.
        ['48-51', '48-51'],
        ['20 /21', '20-21'],
      ]) {
        const created = await request(app.getHttpServer())
          .post('/api/interests')
          .send({ size: entrada })
          .expect(201);
        expect(created.body.size, `entrada «${entrada}»`).toBe(esperada);
      }
    } finally {
      await app.close();
    }
  });

  it('guarda el color en canónico, venga como venga (#49)', async () => {
    // Mismo razonamiento que la talla: un interés guardado con 'VERDE' solo casaría con la tienda
    // que lo escribe así, y el aviso no llegaría para la misma prenda de la de al lado.
    const user = await seedUser(sql, 'kc-color-canon');
    const app = await makeApp(user);
    try {
      for (const [entrada, esperada] of [
        ['VERDE', 'verde'],
        ['Azul Marino', 'azul marino'],
        ['  Gris   Topo ', 'gris topo'],
        ['verde', 'verde'],
      ]) {
        const created = await request(app.getHttpServer())
          .post('/api/interests')
          .send({ color: entrada })
          .expect(201);
        expect(created.body.color, `entrada «${entrada}»`).toBe(esperada);
      }
    } finally {
      await app.close();
    }
  });

  it('rechaza un color sin etiqueta canónica en vez de suscribir a todos (#51)', async () => {
    // `color_canon('771')` es NULL: Zara escribe ahí el id del color, no un nombre. Guardarlo tal
    // cual dejaría `interest.color` a NULL, y para el matching eso significa «cualquier color» —
    // el usuario habría pedido un color y recibiría avisos de todos, sin enterarse.
    const user = await seedUser(sql, 'kc-color-mudo');
    const app = await makeApp(user);
    try {
      for (const mudo of ['771', '107']) {
        await request(app.getHttpServer())
          .post('/api/interests')
          .send({ color: mudo })
          .expect(400);
      }
      // Y no ha quedado nada guardado que avise de más de la cuenta.
      const listed = await request(app.getHttpServer()).get('/api/interests').expect(200);
      expect(listed.body).toHaveLength(0);
    } finally {
      await app.close();
    }
  });

  it('rechaza un interés vacío (sin objetivo ni filtro) con 400', async () => {
    const user = await seedUser(sql, 'kc-sub-empty');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer()).post('/api/interests').send({}).expect(400);
    } finally {
      await app.close();
    }
  });

  it('no permite borrar el interés de otro usuario (404)', async () => {
    const owner = await seedUser(sql, 'kc-owner');
    const ownerApp = await makeApp(owner);
    let interestId: number;
    try {
      const created = await request(ownerApp.getHttpServer())
        .post('/api/interests')
        .send({ category: 'pantalones' })
        .expect(201);
      interestId = created.body.id;
    } finally {
      await ownerApp.close();
    }

    const other = await seedUser(sql, 'kc-other');
    const otherApp = await makeApp(other);
    try {
      await request(otherApp.getHttpServer()).delete(`/api/interests/${interestId}`).expect(404);
    } finally {
      await otherApp.close();
    }
  });

  it('dejar de seguir NO borra los avisos ya entregados (#149)', async () => {
    // La regresión de la issue. `notification.interest_id` es ON DELETE CASCADE, así que mientras el
    // DELETE fue físico este clic se llevaba por delante el historial de avisos — que es la
    // evidencia de que el producto cumple su promesa, y el usuario cree estar diciendo otra cosa.
    const user = await seedUser(sql, 'kc-baja-logica');
    const app = await makeApp(user);
    try {
      const created = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ productId: ids.productId })
        .expect(201);
      const id: number = created.body.id;

      await sql`
        INSERT INTO notification (user_id, interest_id, variant_id, price, price_event_key)
        VALUES (${user.id}, ${id}, ${ids.variantId}, 19.99, '7:19.99')`;

      await request(app.getHttpServer()).delete(`/api/interests/${id}`).expect(204);

      // Desaparece de la lista del usuario, que es lo que él ha pedido...
      await request(app.getHttpServer())
        .get('/api/interests')
        .expect(200)
        .expect((r) => expect(r.body).toHaveLength(0));

      // ...pero la fila sigue viva, inactiva, y el aviso entregado con ella.
      const [fila] = await sql<{ active: boolean }[]>`SELECT active FROM interest WHERE id = ${id}`;
      expect(fila.active).toBe(false);
      const avisos = await sql`SELECT id FROM notification WHERE interest_id = ${id}`;
      expect(avisos).toHaveLength(1);
    } finally {
      await app.close();
    }
  });

  it('volver a seguir lo mismo reactiva la fila y conserva la protección del UNIQUE (#149)', async () => {
    // La otra mitad del arreglo: conservar el historial sin conservar el `interest_id` no arreglaría
    // nada, porque el `UNIQUE (interest_id, variant_id, price_event_key)` cuelga de ese id y el
    // aviso del mismo evento de precio volvería a salir por la puerta de al lado.
    const user = await seedUser(sql, 'kc-re-alta');
    const app = await makeApp(user);
    try {
      const primera = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ productId: ids.productId, minDiscountPct: 30 })
        .expect(201);
      const id: number = primera.body.id;

      await sql`
        INSERT INTO notification (user_id, interest_id, variant_id, price, price_event_key)
        VALUES (${user.id}, ${id}, ${ids.variantId}, 19.99, '7:19.99')`;

      await request(app.getHttpServer()).delete(`/api/interests/${id}`).expect(204);

      // Vuelve a seguir lo mismo, y de paso cambia de opinión sobre el umbral.
      const segunda = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ productId: ids.productId, minDiscountPct: 40 })
        .expect(201);
      expect(segunda.body.id).toBe(id);
      expect(segunda.body.minDiscountPct).toBe('40.00');
      expect(segunda.body.active).toBe(true);

      // Una sola fila, no dos equivalentes.
      const filas = await sql`SELECT id FROM interest WHERE user_id = ${user.id}`;
      expect(filas).toHaveLength(1);

      // Y el mismo evento de precio sigue sin poder avisar dos veces.
      await expect(
        sql`
          INSERT INTO notification (user_id, interest_id, variant_id, price, price_event_key)
          VALUES (${user.id}, ${id}, ${ids.variantId}, 19.99, '7:19.99')`,
      ).rejects.toThrow(/duplicate key|unique/i);
    } finally {
      await app.close();
    }
  });

  it('la re-alta reconoce el mismo alcance aunque la talla venga en crudo (#149)', async () => {
    // El alcance se compara por lo GUARDADO, que es canónico: si no fuera así, seguir el '26' desde
    // el chip y el '26 (16,3 cm)' desde la API abrirían dos intereses para la misma prenda y el
    // historial quedaría partido en dos.
    const user = await seedUser(sql, 'kc-re-alta-canon');
    const app = await makeApp(user);
    try {
      const primera = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ size: '26' })
        .expect(201);
      const segunda = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ size: '26 (16,3 cm)' })
        .expect(201);
      expect(segunda.body.id).toBe(primera.body.id);

      const filas = await sql`SELECT id FROM interest WHERE user_id = ${user.id}`;
      expect(filas).toHaveLength(1);
    } finally {
      await app.close();
    }
  });

  it('dos intereses de «cualquier talla» son el mismo interés (NULLS NOT DISTINCT, #149)', async () => {
    // El motivo de que la 0025 lleve NULLS NOT DISTINCT. Aquí un NULL significa «cualquiera», no
    // «desconocido»: con la semántica por defecto de Postgres estas dos altas no colisionarían, el
    // ON CONFLICT no dispararía nunca y la reactivación no llegaría a ocurrir.
    const user = await seedUser(sql, 'kc-nulls');
    const app = await makeApp(user);
    try {
      const primera = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ section: 'zapateria' })
        .expect(201);
      await request(app.getHttpServer()).delete(`/api/interests/${primera.body.id}`).expect(204);

      const segunda = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ section: 'zapateria' })
        .expect(201);
      expect(segunda.body.id).toBe(primera.body.id);
    } finally {
      await app.close();
    }
  });
});
