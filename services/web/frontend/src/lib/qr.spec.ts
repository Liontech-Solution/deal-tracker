import { describe, expect, it } from 'vitest';

import { qrModules } from './qr';

/** Un deep-link como el que emite `startTelegramLink`: bot real y token `base64url` de 24 bytes. */
const DEEP_LINK = 'https://t.me/deal_tracker_bot?start=Zm9vYmFyYmF6cXV1eDEyMzQ1Njc4OTA';

/**
 * El tamaño de un QR es `4 * versión + 17`, o sea 21, 25, 29… siempre impar y ≡ 1 (mod 4).
 * Lo comprobamos porque es la señal más barata de que la matriz sale de un codificador de verdad
 * y no de un bucle nuestro que devuelve cualquier cosa cuadrada.
 */
function esTamanoDeQr(n: number): boolean {
  return n >= 21 && n <= 177 && (n - 17) % 4 === 0;
}

describe('qrModules (#266)', () => {
  it('devuelve una matriz cuadrada con un tamaño de QR válido', () => {
    const m = qrModules(DEEP_LINK);
    expect(esTamanoDeQr(m.length)).toBe(true);
    for (const fila of m) expect(fila).toHaveLength(m.length);
  });

  it('es determinista: el mismo texto da la misma matriz', () => {
    expect(qrModules(DEEP_LINK)).toEqual(qrModules(DEEP_LINK));
  });

  it('un texto más largo no cabe en una versión más pequeña', () => {
    const corto = qrModules('https://t.me/b?start=x');
    const largo = qrModules(DEEP_LINK + DEEP_LINK + DEEP_LINK);
    expect(largo.length).toBeGreaterThan(corto.length);
  });

  it('rechaza el texto vacío en vez de emitir un QR sin contenido', () => {
    expect(() => qrModules('')).toThrow();
  });
});

/**
 * Los tres patrones de detección son lo que el lector busca para orientar el código: si están mal,
 * la cámara no engancha aunque los datos sean correctos. Es justo el fallo que ningún test de
 * «devuelve algo» detecta y que en pantalla se ve como un QR normal que nadie consigue escanear.
 */
describe('patrones de detección', () => {
  const PATRON = [
    '#######',
    '#.....#',
    '#.###.#',
    '#.###.#',
    '#.###.#',
    '#.....#',
    '#######',
  ];

  function leer(m: boolean[][], fila0: number, col0: number): string[] {
    return PATRON.map((_, r) =>
      PATRON[r]
        .split('')
        .map((_, c) => (m[fila0 + r][col0 + c] ? '#' : '.'))
        .join(''),
    );
  }

  it('están en las tres esquinas: arriba-izquierda, arriba-derecha y abajo-izquierda', () => {
    const m = qrModules(DEEP_LINK);
    const n = m.length;
    expect(leer(m, 0, 0)).toEqual(PATRON);
    expect(leer(m, 0, n - 7)).toEqual(PATRON);
    expect(leer(m, n - 7, 0)).toEqual(PATRON);
  });

  it('la esquina abajo-derecha NO lleva patrón: es lo que da la orientación', () => {
    const m = qrModules(DEEP_LINK);
    const n = m.length;
    expect(leer(m, n - 7, n - 7)).not.toEqual(PATRON);
  });

  it('el patrón de temporización de la fila 6 alterna módulo a módulo', () => {
    const m = qrModules(DEEP_LINK);
    for (let col = 8; col <= m.length - 9; col += 1) {
      expect(m[6][col]).toBe(!m[6][col - 1]);
    }
  });
});
