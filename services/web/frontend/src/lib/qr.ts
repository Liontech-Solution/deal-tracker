/**
 * Generación del QR del deep-link de Telegram (#266).
 *
 * Aquí solo vive la parte pura —texto a matriz de módulos—, y no el SVG, por dos motivos:
 *
 * 1. `qrcode-generator` sabe emitir el SVG él solo (`createSvgTag`), pero devuelve una **cadena de
 *    HTML** que habría que inyectar con `dangerouslySetInnerHTML` y que trae sus propios colores
 *    horneados. Con la matriz, el componente pinta el SVG en JSX y usa `currentColor`, así que
 *    respeta el tema claro/oscuro como el resto de la interfaz.
 * 2. Una matriz de booleanos **se puede testear**. `vitest.config.ts` deja dicho que en `frontend/`
 *    no hay jsdom ni testing-library, o sea que un componente no se puede renderizar en un spec.
 *    Esta frontera es lo que mantiene el QR bajo prueba en vez de bajo palabra.
 */
import qrcode from 'qrcode-generator';

/**
 * Nivel de corrección de errores. `M` (~15 % recuperable) es el equilibrio habitual: `L` deja el
 * código frágil a un reflejo en la pantalla y `Q`/`H` lo hacen más denso —más módulos en el mismo
 * hueco— sin que aquí haga falta, porque el QR se escanea de cerca y de una pantalla, no de una
 * etiqueta impresa y arrugada.
 */
const ERROR_CORRECTION = 'M';

/**
 * Módulos del QR de `text`, como matriz `[fila][columna]` donde `true` es módulo oscuro.
 *
 * El tipo `0` deja que la librería elija la versión más pequeña que dé cabida al contenido, así
 * que el tamaño de la matriz depende del largo del texto y no está fijado de antemano. El modo es
 * `Byte` porque el token es `base64url` y el modo alfanumérico del estándar no cubre minúsculas.
 */
export function qrModules(text: string): boolean[][] {
  if (!text) throw new Error('qrModules: texto vacío');

  const qr = qrcode(0, ERROR_CORRECTION);
  qr.addData(text, 'Byte');
  qr.make();

  const count = qr.getModuleCount();
  return Array.from({ length: count }, (_, row) =>
    Array.from({ length: count }, (_, col) => qr.isDark(row, col)),
  );
}
