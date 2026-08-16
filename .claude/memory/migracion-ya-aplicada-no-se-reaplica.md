---
name: migracion-ya-aplicada-no-se-reaplica
description: editar una migración que los tests ya aplicaron no vuelve a entrar, y el test rojo acusa a tu código
metadata:
  type: project
---

Los dos aplicadores son idempotentes **por número de fichero** contra `schema_migrations`, y eso
muerde mientras desarrollas una migración nueva: si una tanda de tests ya la aplicó y luego editas
el fichero, **el cambio no vuelve a entrar**. La base se queda con la versión vieja y el test falla
señalando tu código, no la causa real.

Pasó el 16/08/2026 con la `0041` de #435: renombré la restricción a `favorite_user_product_uniq`
después de que un `pnpm test` la hubiera aplicado sin nombre, y el test seguía leyendo
`favorite_user_id_product_id_key` de `information_schema`.

**Why:** el síntoma no apunta a la causa. Parece un fallo del código nuevo, y se pierde un rato
buscándolo ahí antes de sospechar del aplicador.

**How to apply:** al editar una migración que ya has ejecutado, límpiala a mano en **cada** base de
la sesión antes de volver a probar — `DROP TABLE <lo que creara> CASCADE` y
`DELETE FROM schema_migrations WHERE version = '00NN_<nombre>.sql'`. Son tres bases si el web está
en juego (la de la pasada, la de pytest/vitest y la de ctype `C`), y olvidar una deja el fallo vivo
solo en esa. Ver [[web-tests-sin-env-con-docker]] y [[verificar-en-cluster-dev]].
