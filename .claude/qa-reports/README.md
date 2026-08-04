# Informes de validación de QA

Un fichero por versión validada, `<vX.Y.Z>.md`, escrito por `/validar-qa`.

Se versionan en git a propósito, por dos razones:

1. **Son el registro de por qué se promovió (o no) cada versión.** Un `release-qa` sin informe al
   lado es una decisión sin motivo escrito.
2. **El bloque `## Cifras` de cada informe es la línea base del siguiente.** Es lo único que permite
   detectar que una tienda pasó de 3381 productos a 40 con la pasada cerrando en `success`. Sin el
   informe anterior, la validación siguiente no puede ver regresiones y tiene que decirlo.

No los edites a mano para «arreglar» un veredicto. Si algo se corrigió después, se valida otra vez y
se escribe el informe de la versión nueva.
