import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type postgres from 'postgres';

import type { EmailMessage, EmailSendResult } from '../src/email/email.types';
import type { CreateUserResult } from '../src/keycloak-admin/keycloak-admin.types';
import { makeApp, makeSql, resetSchema, TEST_DB } from './helpers';

/**
 * El alta por invitación de punta a punta (#549), sobre BD real.
 *
 * **Los dos sistemas de fuera van doblados**, y no por comodidad: `KeycloakAdminClient` crearía
 * usuarios de verdad en el realm y `EmailApiClient` gastaría envíos reales de Resend. Doblados, lo
 * que queda por comprobar es justo lo que este servicio decide — el cupo, la unicidad del correo,
 * el estado del token y el orden de las escrituras del alta.
 *
 * Lo que **no** se puede comprobar aquí, y hay que decirlo para que el verde no prometa de más: que
 * Keycloak acepte de verdad la creación y que el correo llegue a un buzón. Eso solo se observa en
 * QA, después del merge, y es lo que la SZ (#552) convierte en casos de `/validar-qa`.
 */

/** Cabecera de un usuario de sesión mutable: `makeApp` guarda la referencia, así que cambiarle los
 * campos entre pruebas cambia quién hace la petición sin levantar otra app. */
const sesion = { id: 0, keycloakSub: 'kc-inv', email: 'quien@invita.test', displayName: 'Quien Invita' };

/** El doble del correo: registra lo enviado y deja fijar el resultado desde cada prueba. */
const correos: EmailMessage[] = [];
let resultadoCorreo: EmailSendResult = { ok: true, id: 'email-1' };
const emailDoble = {
  sendEmail: async (message: EmailMessage): Promise<EmailSendResult> => {
    correos.push(message);
    return resultadoCorreo;
  },
};

/** El doble de Keycloak: cuenta altas y deja fijar el resultado (incluido el 409 de `exists`). */
let altas = 0;
let resultadoAlta: CreateUserResult = { ok: true, userId: 'kc-nuevo' };
const keycloakDoble = {
  createUser: async (): Promise<CreateUserResult> => {
    altas += 1;
    return resultadoAlta;
  },
};

/** El token que viaja en el correo, que es el único sitio donde existe en claro. */
function tokenDelUltimoCorreo(): string {
  const url = correos[correos.length - 1]?.text ?? '';
  const match = url.match(/[?&]token=([A-Za-z0-9_-]+)/);
  if (!match) throw new Error(`El correo no lleva token: ${url}`);
  return match[1];
}

describe.skipIf(!TEST_DB)('alta por invitación (e2e)', () => {
  let sql: postgres.Sql;

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  /**
   * Con el registro apagado los cinco responden 503 antes de tocar nada. Es la rama que corre en
   * `dev` **siempre**, porque su overlay borra las `KEYCLOAK_*` a propósito: sin este bloque, un
   * `dev` en verde no diría absolutamente nada del alta (#309).
   */
  describe('sin registro configurado', () => {
    let app: INestApplication;

    beforeAll(async () => {
      await resetSchema(sql);
      const [u] = await sql<{ id: number }[]>`
        INSERT INTO app_user (keycloak_sub, email, display_name, invites_remaining)
        VALUES ('kc-apagado', 'quien@invita.test', 'Quien Invita', 5) RETURNING id`;
      sesion.id = u.id;
      vi.resetModules();
      app = await makeApp(sesion, [
        { provide: (await import('../src/email/email-api.client')).EmailApiClient, useValue: emailDoble },
        {
          provide: (await import('../src/keycloak-admin/keycloak-admin.client')).KeycloakAdminClient,
          useValue: keycloakDoble,
        },
      ]);
    });

    afterAll(async () => {
      await app.close();
    });

    it('los cinco endpoints responden 503', async () => {
      const http = app.getHttpServer();
      await request(http).post('/api/invitations').send({ email: 'a@b.test' }).expect(503);
      await request(http).get('/api/invitations').expect(503);
      await request(http).delete('/api/invitations/1').expect(503);
      await request(http).get('/api/invitations/token/loquesea').expect(503);
      await request(http)
        .post('/api/invitations/token/loquesea/accept')
        .send({ password: 'contrasenya-larga' })
        .expect(503);
    });

    it('no gasta cupo ni escribe nada', async () => {
      const [u] = await sql<{ n: number }[]>`
        SELECT invites_remaining AS n FROM app_user WHERE id = ${sesion.id}`;
      expect(u.n).toBe(5);
      const [{ n }] = await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM invitation`;
      expect(n).toBe(0);
    });
  });

  describe('con registro configurado', () => {
    let app: INestApplication;
    let otroUsuario: number;
    const guardado = {
      issuer: process.env.KEYCLOAK_ISSUER_URL,
      audience: process.env.KEYCLOAK_AUDIENCE,
      adminSecret: process.env.KEYCLOAK_ADMIN_CLIENT_SECRET,
      resend: process.env.RESEND_API_KEY,
      publicUrl: process.env.APP_PUBLIC_URL,
    };

    beforeAll(async () => {
      // Las tres condiciones de `isInvitesConfigured()` más la URL pública, que no está en el
      // interruptor y sin la cual el enlace del correo saldría roto.
      process.env.KEYCLOAK_ISSUER_URL = 'https://keycloak.invalido/realms/deal-tracker';
      process.env.KEYCLOAK_AUDIENCE = 'deal-tracker-web';
      process.env.KEYCLOAK_ADMIN_CLIENT_SECRET = 'secreto-de-pega';
      process.env.RESEND_API_KEY = 're_de_pega';
      process.env.APP_PUBLIC_URL = 'https://dealtracker.test';
      vi.resetModules();
      app = await makeApp(sesion, [
        { provide: (await import('../src/email/email-api.client')).EmailApiClient, useValue: emailDoble },
        {
          provide: (await import('../src/keycloak-admin/keycloak-admin.client')).KeycloakAdminClient,
          useValue: keycloakDoble,
        },
      ]);
    });

    afterAll(async () => {
      await app.close();
      for (const [clave, valor] of [
        ['KEYCLOAK_ISSUER_URL', guardado.issuer],
        ['KEYCLOAK_AUDIENCE', guardado.audience],
        ['KEYCLOAK_ADMIN_CLIENT_SECRET', guardado.adminSecret],
        ['RESEND_API_KEY', guardado.resend],
        ['APP_PUBLIC_URL', guardado.publicUrl],
      ] as const) {
        if (valor === undefined) delete process.env[clave];
        else process.env[clave] = valor;
      }
      vi.resetModules();
    });

    beforeEach(async () => {
      await resetSchema(sql);
      correos.length = 0;
      altas = 0;
      resultadoCorreo = { ok: true, id: 'email-1' };
      resultadoAlta = { ok: true, userId: 'kc-nuevo' };
      const [quien] = await sql<{ id: number }[]>`
        INSERT INTO app_user (keycloak_sub, email, display_name, invites_remaining)
        VALUES ('kc-inv', 'quien@invita.test', 'Quien Invita', 2) RETURNING id`;
      const [otro] = await sql<{ id: number }[]>`
        INSERT INTO app_user (keycloak_sub, email, display_name, invites_remaining)
        VALUES ('kc-otro', 'otro@x.test', 'Otro', 2) RETURNING id`;
      sesion.id = quien.id;
      otroUsuario = otro.id;
    });

    // Sin `async`: devuelve el encadenable de supertest, para poder pegarle `.expect()` detrás.
    function invitar(email: string) {
      return request(app.getHttpServer()).post('/api/invitations').send({ email });
    }

    async function cupo(userId = sesion.id): Promise<number> {
      const [u] = await sql<{ n: number }[]>`
        SELECT invites_remaining AS n FROM app_user WHERE id = ${userId}`;
      return u.n;
    }

    it('sin token -> 401 en los tres endpoints con sesión', async () => {
      // Guard real: aquí no se dobla nadie. Los dos públicos no salen porque no llevan guard, que
      // es justamente lo que los hace públicos.
      const sinSesion = await makeApp();
      try {
        const http = sinSesion.getHttpServer();
        await request(http).post('/api/invitations').send({ email: 'a@b.test' }).expect(401);
        await request(http).get('/api/invitations').expect(401);
        await request(http).delete('/api/invitations/1').expect(401);
      } finally {
        await sinSesion.close();
      }
    });

    it('invitar gasta cupo, guarda el hash y manda el enlace con el token', async () => {
      const res = await invitar('Ana.Perez@Example.com ').expect(201);

      // El correo se normaliza EN LA APLICACIÓN: el índice va sobre la columna desnuda porque con
      // el ctype C del cluster `lower()` no baja las acentuadas (#105).
      expect(res.body).toMatchObject({ email: 'ana.perez@example.com', invitesRemaining: 1 });
      expect(await cupo()).toBe(1);

      const [fila] = await sql<{ email: string; token_hash: string; dias: number }[]>`
        SELECT email, token_hash, round(extract(epoch FROM expires_at - now()) / 86400)::int AS dias
        FROM invitation`;
      expect(fila.email).toBe('ana.perez@example.com');
      // El token viaja al correo y a la base solo su sha256: ni el hash es el token, ni el token
      // está en ninguna columna (decisión 2 de la 0044).
      const token = tokenDelUltimoCorreo();
      expect(fila.token_hash).toHaveLength(64);
      expect(fila.token_hash).not.toBe(token);
      expect(fila.dias).toBe(7);

      expect(correos[0].to).toBe('ana.perez@example.com');
      expect(correos[0].text).toContain('https://dealtracker.test/registro?token=');
      expect(correos[0].subject).toContain('Quien Invita');
    });

    it('sin cupo -> 403 y no escribe invitación', async () => {
      await sql`UPDATE app_user SET invites_remaining = 0 WHERE id = ${sesion.id}`;
      await invitar('sin@cupo.test').expect(403);
      const [{ n }] = await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM invitation`;
      expect(n).toBe(0);
      expect(correos).toHaveLength(0);
      expect(await cupo()).toBe(0);
    });

    it('un correo ya invitado -> 409 y el cupo vuelve', async () => {
      await invitar('ana@example.com').expect(201);
      expect(await cupo()).toBe(1);

      const res = await invitar('ana@example.com').expect(409);
      expect(res.body.message).toMatch(/revóc/i);
      // Lo importante del caso: el segundo intento no cobra. El 23505 llega del índice PARCIAL, no
      // de un UNIQUE a secas, así que la vuelta del cupo hay que hacerla a mano.
      expect(await cupo()).toBe(1);
      expect(correos).toHaveLength(1);
    });

    it('una invitación CADUCADA sigue ocupando el correo, y el mensaje lleva a revocarla', async () => {
      // Es el caso que sorprende y por el que el mensaje no puede ser «ya tiene invitación»: el
      // predicado del índice no puede mirar `expires_at` (Postgres no admite `now()` ahí), así que
      // una caducada bloquea igual. La salida es revocar, que además devuelve el cupo.
      await invitar('ana@example.com').expect(201);
      await sql`UPDATE invitation SET expires_at = now() - interval '1 day'`;

      await invitar('ana@example.com').expect(409);
      expect(await cupo()).toBe(1);

      const [inv] = await sql<{ id: number }[]>`SELECT id FROM invitation`;
      await request(app.getHttpServer()).delete(`/api/invitations/${inv.id}`).expect(204);
      expect(await cupo()).toBe(2);

      // Y ahora sí: revocar es lo que libera el correo.
      await invitar('ana@example.com').expect(201);
      expect(await cupo()).toBe(1);
    });

    it('si el correo no sale, se borra la invitación y se devuelve el cupo', async () => {
      resultadoCorreo = { ok: false, reason: 'network' };
      await invitar('ana@example.com').expect(502);

      expect(await cupo()).toBe(2);
      const [{ n }] = await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM invitation`;
      // Se borra en vez de revocarse: nada salió del servidor, así que no hay nada que contar en la
      // lista de quien invita, y borrarla libera el correo para el reintento.
      expect(n).toBe(0);

      resultadoCorreo = { ok: true, id: 'email-2' };
      await invitar('ana@example.com').expect(201);
    });

    it('la lista enseña el correo entero y el estado calculado', async () => {
      await invitar('viva@example.com').expect(201);
      await invitar('caducada@example.com').expect(201);
      await sql`UPDATE invitation SET expires_at = now() - interval '1 day'
                 WHERE email = 'caducada@example.com'`;

      const res = await request(app.getHttpServer()).get('/api/invitations').expect(200);
      const porCorreo = Object.fromEntries(
        (res.body as { email: string; status: string }[]).map((i) => [i.email, i.status]),
      );
      expect(porCorreo).toEqual({ 'viva@example.com': 'viva', 'caducada@example.com': 'caducada' });
      // Ni el token ni su hash salen nunca por HTTP.
      expect(JSON.stringify(res.body)).not.toContain('token');
    });

    it('la lista es solo la mía', async () => {
      await invitar('mia@example.com').expect(201);
      await sql`
        INSERT INTO invitation (inviter_user_id, email, token_hash, expires_at)
        VALUES (${otroUsuario}, 'ajena@example.com', 'hash-ajeno', now() + interval '7 days')`;

      const res = await request(app.getHttpServer()).get('/api/invitations').expect(200);
      expect(res.body).toHaveLength(1);
      expect(res.body[0].email).toBe('mia@example.com');
    });

    it('revocar la de otro no la toca, y devuelve 404', async () => {
      const [ajena] = await sql<{ id: number }[]>`
        INSERT INTO invitation (inviter_user_id, email, token_hash, expires_at)
        VALUES (${otroUsuario}, 'ajena@example.com', 'hash-ajeno', now() + interval '7 days')
        RETURNING id`;

      await request(app.getHttpServer()).delete(`/api/invitations/${ajena.id}`).expect(404);
      const [fila] = await sql<{ revoked_at: Date | null }[]>`
        SELECT revoked_at FROM invitation WHERE id = ${ajena.id}`;
      expect(fila.revoked_at).toBeNull();
      // Y no ha devuelto cupo a nadie: ni al que lo pidió ni al dueño.
      expect(await cupo()).toBe(2);
      expect(await cupo(otroUsuario)).toBe(2);
    });

    it('revocar dos veces no devuelve cupo dos veces', async () => {
      await invitar('ana@example.com').expect(201);
      const [inv] = await sql<{ id: number }[]>`SELECT id FROM invitation`;

      await request(app.getHttpServer()).delete(`/api/invitations/${inv.id}`).expect(204);
      expect(await cupo()).toBe(2);
      await request(app.getHttpServer()).delete(`/api/invitations/${inv.id}`).expect(404);
      expect(await cupo()).toBe(2);
    });

    it('revocar una YA ACEPTADA no devuelve cupo: ésa se gastó de verdad', async () => {
      await invitar('ana@example.com').expect(201);
      await sql`UPDATE invitation SET accepted_at = now()`;
      const [inv] = await sql<{ id: number }[]>`SELECT id FROM invitation`;

      await request(app.getHttpServer()).delete(`/api/invitations/${inv.id}`).expect(404);
      expect(await cupo()).toBe(1);
    });

    describe('el token, visto por quien no tiene cuenta', () => {
      function verToken(token: string) {
        return request(app.getHttpServer()).get(`/api/invitations/token/${token}`).expect(200);
      }

      it('válido: dice a qué correo invita y quién invita', async () => {
        await invitar('ana@example.com').expect(201);
        const res = await verToken(tokenDelUltimoCorreo());
        expect(res.body).toMatchObject({
          status: 'valida',
          email: 'ana@example.com',
          inviterName: 'Quien Invita',
        });
      });

      it('caducado, canjeado, revocado e inexistente: 200 con su estado', async () => {
        await invitar('ana@example.com').expect(201);
        const token = tokenDelUltimoCorreo();

        await sql`UPDATE invitation SET expires_at = now() - interval '1 day'`;
        expect((await verToken(token)).body).toEqual({ status: 'caducada' });

        await sql`UPDATE invitation SET expires_at = now() + interval '1 day', accepted_at = now()`;
        expect((await verToken(token)).body).toEqual({ status: 'canjeada' });

        // Revocada se colapsa en `desconocida`: quien invitó se la quitó y no hay nada que contar.
        await sql`UPDATE invitation SET accepted_at = NULL, revoked_at = now()`;
        expect((await verToken(token)).body).toEqual({ status: 'desconocida' });

        expect((await verToken('token-que-no-existe')).body).toEqual({ status: 'desconocida' });
      });

      it('ninguna respuesta filtra el correo salvo la válida', async () => {
        await invitar('ana@example.com').expect(201);
        const token = tokenDelUltimoCorreo();
        await sql`UPDATE invitation SET expires_at = now() - interval '1 day'`;
        expect(JSON.stringify((await verToken(token)).body)).not.toContain('ana@example.com');
      });
    });

    describe('el alta', () => {
      function aceptar(token: string, body: Record<string, unknown> = { password: 'contrasenya-larga' }) {
        return request(app.getHttpServer()).post(`/api/invitations/token/${token}/accept`).send(body);
      }

      it('crea la cuenta y cierra la invitación', async () => {
        await invitar('ana@example.com').expect(201);
        const token = tokenDelUltimoCorreo();

        const res = await aceptar(token).expect(201);
        expect(res.body).toEqual({ email: 'ana@example.com' });
        expect(altas).toBe(1);

        const [fila] = await sql<{ accepted_at: Date | null; accepted_user_id: number | null }[]>`
          SELECT accepted_at, accepted_user_id::int FROM invitation`;
        expect(fila.accepted_at).not.toBeNull();
        // Todavía NULL, y no es un olvido: la fila de `app_user` nace en la primera petición
        // autenticada. La rellena `UserService`, y eso se comprueba en su propia prueba.
        expect(fila.accepted_user_id).toBeNull();
      });

      it('el token es de un solo uso', async () => {
        await invitar('ana@example.com').expect(201);
        const token = tokenDelUltimoCorreo();

        await aceptar(token).expect(201);
        await aceptar(token).expect(410);
        // La segunda no llegó a Keycloak: se corta antes de crear nada.
        expect(altas).toBe(1);
      });

      it('caducada, revocada e inexistente -> 410, sin crear nada', async () => {
        await invitar('ana@example.com').expect(201);
        const token = tokenDelUltimoCorreo();

        await sql`UPDATE invitation SET expires_at = now() - interval '1 day'`;
        await aceptar(token).expect(410);

        await sql`UPDATE invitation SET expires_at = now() + interval '7 days', revoked_at = now()`;
        await aceptar(token).expect(410);

        await aceptar('token-que-no-existe').expect(410);
        expect(altas).toBe(0);
      });

      it('el correo lo fija la invitación: mandar otro en el cuerpo es un 400', async () => {
        // El DTO no tiene campo `email` y el ValidationPipe global va con `forbidNonWhitelisted`,
        // así que el intento de darse de alta con otra dirección ni siquiera se ignora en silencio.
        await invitar('ana@example.com').expect(201);
        const token = tokenDelUltimoCorreo();
        await aceptar(token, { password: 'contrasenya-larga', email: 'otro@ladron.test' }).expect(400);
        expect(altas).toBe(0);
      });

      it('una contraseña corta no llega a Keycloak', async () => {
        await invitar('ana@example.com').expect(201);
        await aceptar(tokenDelUltimoCorreo(), { password: 'corta' }).expect(400);
        expect(altas).toBe(0);
      });

      it('si Keycloak dice que ya existe, cierra la invitación y manda a acceder', async () => {
        await invitar('ana@example.com').expect(201);
        const token = tokenDelUltimoCorreo();
        resultadoAlta = { ok: false, reason: 'exists' };

        const res = await aceptar(token).expect(409);
        expect(res.body.code).toBe('ya_registrado');
        // La invitación se cierra igual: el token deja de servir, que es lo que permite que un
        // reintento no se quede en un bucle.
        const [fila] = await sql<{ accepted_at: Date | null }[]>`SELECT accepted_at FROM invitation`;
        expect(fila.accepted_at).not.toBeNull();
        await aceptar(token).expect(410);
      });

      it('si Keycloak falla de verdad, 502 y la invitación sigue viva', async () => {
        await invitar('ana@example.com').expect(201);
        const token = tokenDelUltimoCorreo();
        resultadoAlta = { ok: false, reason: 'network' };

        await aceptar(token).expect(502);
        const [fila] = await sql<{ accepted_at: Date | null }[]>`SELECT accepted_at FROM invitation`;
        // Sigue viva a propósito: es el desenlace elegido del orden de escrituras. Quien no pudo
        // darse de alta puede reintentar con el mismo enlace.
        expect(fila.accepted_at).toBeNull();

        resultadoAlta = { ok: true, userId: 'kc-nuevo' };
        await aceptar(token).expect(201);
      });
    });

    it('el aprovisionamiento JIT enlaza la invitación con la cuenta que nació de ella', async () => {
      await invitar('Ana@Example.com').expect(201);
      const token = tokenDelUltimoCorreo();
      await request(app.getHttpServer())
        .post(`/api/invitations/token/${token}/accept`)
        .send({ password: 'contrasenya-larga' })
        .expect(201);

      const { UserService } = await import('../src/auth/user.service');
      const users = app.get(UserService);
      // Lo que hará la primera petición autenticada de esa persona. El correo llega del token con
      // la caja que use Keycloak: se normaliza igual que al guardarlo.
      const nuevo = await users.provisionFromClaims({ sub: 'kc-recien-nacido', email: 'ANA@example.com' });

      // `::int` porque postgres.js devuelve los bigint como string.
      const [fila] = await sql<{ accepted_user_id: number | null }[]>`
        SELECT accepted_user_id::int FROM invitation`;
      expect(fila.accepted_user_id).toBe(nuevo.id);

      // Y la segunda petición del mismo usuario NO vuelve a mirar la tabla. Esto no es cosmética:
      // el aprovisionamiento corre en cada petición autenticada, así que el enlace va detrás de
      // `xmax = 0` —«esta fila la acabo de insertar»— y no de un `UPDATE` por petición.
      //
      // Se comprueba con una segunda invitación aceptada y huérfana del mismo correo: si el gate
      // no funcionase, la segunda llamada la adoptaría. Con él, se queda como está.
      await sql`
        INSERT INTO invitation (inviter_user_id, email, token_hash, expires_at, accepted_at)
        VALUES (${sesion.id}, 'ana@example.com', 'hash-huerfano', now() + interval '7 days', now())`;
      await users.provisionFromClaims({ sub: 'kc-recien-nacido', email: 'ANA@example.com' });

      const huerfanas = await sql<{ n: number }[]>`
        SELECT count(*)::int AS n FROM invitation WHERE accepted_user_id IS NULL`;
      expect(huerfanas[0].n).toBe(1);
    });
  });
});
