-- El alta por invitación: la tabla `invitation` y el cupo de quien invita (#546).
--
-- Primera pieza de la v0.8.0 (#554), la versión que convierte en producto lo que hoy es un
-- bloqueo. Hasta ahora las cuentas se daban de alta a mano con `kcadm.sh` —`AccessPage.tsx` lo
-- dice literalmente— y el registro de Keycloak está apagado a propósito. Esto es solo el esquema:
-- el hash del token, el correo y el consumo del cupo son de #547, #548 y #549.
--
-- ── TRES DECISIONES QUE VAN ESCRITAS AQUÍ Y NO SE DEDUCEN DEL DDL ──
--
-- 1 · EL CORREO SE NORMALIZA EN LA APLICACIÓN, Y EL ÍNDICE VA SOBRE LA COLUMNA DESNUDA.
--
-- La tentación es `UNIQUE (lower(email))`, y aquí eso es una trampa que este repo ya pagó: la
-- base del cluster es `UTF8 | C | C` y con ctype `C` `lower()` NO baja las acentuadas (#105). Un
-- índice funcional se comportaría distinto en CI que en el cluster, que es exactamente el fallo
-- que aquella issue costó descubrir. Se guarda ya en minúsculas y recortado, y el índice es
-- literal. Tampoco hay CHECK de «está en minúsculas»: lo escribiría con el mismo `lower()` y
-- heredaría el mismo problema.
--
-- 2 · SE GUARDA EL HASH DEL TOKEN, NO EL TOKEN.
--
-- Diverge A PROPÓSITO del precedente que vive en la tabla de al lado: `app_user.telegram_link_token`
-- (0006) se guarda en claro. La diferencia es lo que cada token concede — aquél enlaza un chat de
-- Telegram a una cuenta que YA existe, éste CREA la cuenta. Un volcado de la base no debe bastar
-- para darse de alta como otra persona. Que quede dicho para que nadie lea la divergencia como un
-- descuido ni «unifique» los dos caminos.
--
-- 3 · EL CUPO SE CONSUME EN EL ENVÍO, EN UNA SOLA SENTENCIA.
--
--     UPDATE app_user SET invites_remaining = invites_remaining - 1
--      WHERE id = $1 AND invites_remaining > 0
--
-- Si no toca ninguna fila, no había cupo. Es el mismo truco que ya usa
-- `TelegramLinkService.redeemStartToken()` para resolver «no existe» y «doble canje» sin
-- transacción explícita. REVOCAR DEVUELVE EL CUPO; CADUCAR NO — y por eso revocar una invitación
-- caducada es la forma de recuperarlo, que evita inventar un job de limpieza para algo que a este
-- volumen no lo necesita.
--
-- ── LA CADUCIDAD SON 7 DÍAS, Y EL NÚMERO NO ESTÁ EN ESTE FICHERO ──
--
-- `expires_at` es NOT NULL y SIN default: la ventana la fija quien crea la invitación (#549), como
-- el TTL de Telegram vive en `LINK_TOKEN_TTL_MS` y no en la 0006. Pero el número se elige aquí,
-- porque es política y no implementación.
--
-- Son 7 días. El precedente de Telegram —60 minutos— no sirve: allí el usuario acaba de pulsar el
-- botón y está mirando la pantalla; aquí quien invita NO controla cuándo lee el correo el invitado.
-- Siete días cubren un fin de semana entero y un ciclo de trabajo, que es lo que separa «no lo he
-- visto» de «no me interesa». Y no más, porque el cupo es escaso por diseño (ver abajo) y, como
-- caducar no lo devuelve, cada invitación viva es cupo inmovilizado de quien la mandó.
--
-- ── EL CUPO ARRANCA A CERO, Y ESO ES LA POLÍTICA ──
--
-- `DEFAULT 0` no es un valor de relleno: es la decisión de producto. Nadie puede invitar a nadie
-- mientras no se le dé cupo a mano. Al estrenar, el único con cupo será `test-qa` en QA, que es
-- quien ejerce el flujo en la validación; en prod se reparte por SQL cuando se decida.
--
-- Un detalle operativo que hay que tener presente al repartirlo: la fila de `app_user` NACE EN LA
-- PRIMERA PETICIÓN AUTENTICADA, no al crear el usuario en Keycloak, así que no se puede dar cupo a
-- alguien que todavía no ha entrado nunca. Medido el 20/08/2026: `deal-tracker-prod` tiene 2
-- usuarios en Keycloak y `app_user` en `deal_tracker_prod` tiene 1 fila.
CREATE TABLE IF NOT EXISTS invitation (
    id               BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Se va con quien invitó, como `interest`, `favorite` y `notification`: si la cuenta
    -- desaparece, sus invitaciones pendientes no deben poder canjearse.
    inviter_user_id  BIGINT      NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    -- Ya en minúsculas y recortado por la aplicación. Ver la decisión 1 de la cabecera.
    email            TEXT        NOT NULL,
    -- El hash, nunca el token. Ver la decisión 2. UNIQUE porque el canje busca por aquí y porque
    -- dos invitaciones no pueden compartir secreto.
    token_hash       TEXT        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Sin default: la ventana la decide quien crea la invitación (7 días, ver la cabecera).
    expires_at       TIMESTAMPTZ NOT NULL,
    accepted_at      TIMESTAMPTZ,
    -- SET NULL y no CASCADE: si la cuenta que nació de esta invitación se borra, la invitación
    -- sigue siendo un hecho ocurrido —se gastó cupo y se mandó un correo— y no debe evaporarse.
    -- Lo que se pierde es a quién apuntaba, no que pasara.
    accepted_user_id BIGINT      REFERENCES app_user (id) ON DELETE SET NULL,
    revoked_at       TIMESTAMPTZ,
    CONSTRAINT invitation_token_hash_uniq UNIQUE (token_hash)
);

-- UNA INVITACIÓN VIVA POR CORREO. Las canjeadas y las revocadas no estorban, así que a quien
-- declinó se le puede volver a invitar y a quien ya entró no se le invita dos veces.
--
-- Es el primer `CREATE UNIQUE INDEX` del repo, y el primer `ux_`: hay 19 `ix_` y la unicidad se ha
-- expresado siempre como `CONSTRAINT ..._uniq UNIQUE (...)`. Aquí no se puede, porque un
-- constraint UNIQUE no admite `WHERE` y la regla es parcial por definición. El prefijo distinto es
-- deliberado: dice que este índice es una REGLA DE NEGOCIO y no una optimización, y que borrarlo
-- «porque la tabla es pequeña» cambia el comportamiento. Índices parciales sí había ya
-- (`WHERE active` en la 0004, `WHERE delisted_at IS NULL` en la 0001 y la 0009).
--
-- Y una consecuencia que NO es una elección, medida al escribir esto: una invitación CADUCADA
-- sigue ocupando el sitio. No se puede excluir del predicado, porque Postgres rechaza `now()` en un
-- índice («functions in index predicate must be marked IMMUTABLE»), así que el índice no puede
-- saber qué está vencido. La salida es revocarla, y encaja: revocar es justo lo que devuelve el
-- cupo, de modo que el único gesto que libera el correo es el mismo que recupera la invitación
-- gastada. No hace falta ningún job de limpieza.
--
-- Consecuencias para quien escriba el INSERT (#549): el segundo intento sobre un correo ya invitado
-- llega como un 23505, no como una fila que no aparece — y ese 23505 puede venir de una invitación
-- caducada, así que el mensaje correcto no es «ya tiene invitación» sino algo que lleve a revocarla.
CREATE UNIQUE INDEX IF NOT EXISTS ux_invitation_email_viva ON invitation (email)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

-- NO hay índice sobre `inviter_user_id`, y es una decisión, no un olvido. #551 leerá «a quién he
-- invitado», y el precedente sería `ix_favorite_user`. Pero un favorito es por usuario y sin techo,
-- mientras que las invitaciones de un usuario están acotadas por su cupo, que arranca a 0 y se
-- reparte a mano: esta tabla se mide en decenas de filas y el seqscan gana. Si algún día el cupo se
-- reparte de verdad, el índice se añade entonces y con el número delante.

-- El cupo de quien invita. Ver «EL CUPO ARRANCA A CERO» en la cabecera.
ALTER TABLE app_user ADD COLUMN invites_remaining INTEGER NOT NULL DEFAULT 0;

-- El `UPDATE ... WHERE invites_remaining > 0` ya resuelve la carrera entre peticiones, así que este
-- CHECK no protege del camino normal: protege del OTRO, que es el que existe de verdad aquí. El
-- cupo en prod se reparte A MANO por SQL, y ahí un `- 1` de más no lo para nadie. Cuesta cero y
-- hace el negativo imposible por construcción, que es como este esquema prefiere sus invariantes.
ALTER TABLE app_user ADD CONSTRAINT app_user_invites_remaining_chk CHECK (invites_remaining >= 0);

COMMENT ON TABLE invitation IS
    'Invitaciones al alta por invitación. Guarda el HASH del token, no el token, divergiendo a '
    'propósito de `app_user.telegram_link_token`: aquél enlaza un chat a una cuenta existente y '
    'éste CREA la cuenta. El correo se normaliza en la aplicación y `ux_invitation_email_viva` lo '
    'indexa desnudo, porque con el ctype C del cluster `lower()` no baja las acentuadas (#105). '
    'Ver #546.';

COMMENT ON COLUMN app_user.invites_remaining IS
    'Cuántas invitaciones le quedan a este usuario. Arranca a 0 A PROPÓSITO: nadie invita hasta '
    'que se le da cupo a mano. Se consume en el envío con `UPDATE ... WHERE invites_remaining > 0`; '
    'revocar lo devuelve, caducar no. Ver #546.';
