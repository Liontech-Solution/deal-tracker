---
name: web-tests-sin-env-con-docker
description: "Los tests del servicio web no necesitan `.env` (vitest lee process.env directo) y sus dos bases salen de un Postgres desechable en Docker; escribir `.env` está vetado por permisos"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c80f9c6-54bf-478c-92bd-2d66bb64960a
  modified: 2026-08-06T11:41:41.258Z
---

En esta máquina **no hay `.env` en ninguno de los dos servicios** y tampoco `psql`/`postgres` en el
PATH — pero **sí hay Docker** con `postgres:16`, `postgres:17` y `postgres:16-alpine` ya cacheadas.
La Postgres de usuario de `~/.local/share/pgsql-local` existe pero suele estar **parada**.

Para `services/web`, la ruta que funciona sin pelearse con nada:

```bash
docker run -d --name dt<issue>-pg -e POSTGRES_USER=dealtracker -e POSTGRES_PASSWORD=dealtracker \
  -e POSTGRES_DB=dt_<issue>_test -p 55432:5432 postgres:16-alpine
docker exec dt<issue>-pg psql -U dealtracker -d dt_<issue>_test -c \
  "CREATE DATABASE dt_<issue>_ctype_c TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';"
env TEST_DATABASE_URL=postgresql://dealtracker:dealtracker@localhost:55432/dt_<issue>_test \
    TEST_DATABASE_URL_CTYPE_C=postgresql://dealtracker:dealtracker@localhost:55432/dt_<issue>_ctype_c \
    pnpm test
```

Tres cosas que ahorran tiempo:

- **`vitest.config.ts` no carga dotenv**: los specs leen `process.env` directo, así que las
  variables van en la línea de comandos y **no hace falta crear `.env`**. Mejor así, porque
  **escribir `services/web/.env` lo deniega la configuración de permisos** (la herramienta Write
  falla con «directory is denied»), y copiarlo del checkout original tampoco vale: no existe.
- Las migraciones las aplica el propio `resetSchema` de `test/helpers.ts`, o sea que no hay que
  correr `pnpm migrate` a mano contra las bases de test.
- Puerto **no** 5432: con varias sesiones a la vez conviene uno propio (55432 y siguientes).

El contenedor es de la sesión y se borra al cerrar (`docker rm -f dt<issue>-pg`), que se lleva las
dos bases de dentro.

**Crea el tuyo aunque veas un `dt-pg` parado y reaprovecharlo parezca gratis** — no lo es. Medido
el 06/08/2026: un `docker start` sobre un contenedor que salió con `Exited (128)` (un reinicio de la
máquina) lo deja corriendo **sin ninguna red**. `HostConfig.PortBindings` sigue diciendo `5432`,
pero `NetworkSettings.Ports` y `.Networks` vienen **vacíos**, `docker port` no imprime nada y no hay
nadie escuchando en el host. `docker exec ... psql` funciona —y engaña, porque va por el socket de
dentro—; todo lo que conecte por TCP muere.

El síntoma no se parece a la causa: los specs de integración fallan en bloque (33 de 349) con
`Hook timed out in 10000ms` sobre el `resetSchema` del `beforeEach`, así que parece cosa de las
migraciones o del esquema, y aplicarlas a mano no cambia nada. Lo que lo distingue en un comando es
`ss -ltnp | grep 5432` (nada) o `docker inspect <c> --format '{{json .NetworkSettings.Networks}}'`
(`{}`). Se arregla sin recrear nada con `docker network connect bridge <c>`, y luego se conecta por
la **IP del bridge** (`docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'`,
típicamente `172.17.0.2`), no por `localhost`: el puerto publicado sigue sin existir.

Relacionado: [[scraper-sin-just-ni-env]] (el mismo problema del lado Python),
[[verificar-en-cluster-dev]] (por qué son **dos** bases y no una).
