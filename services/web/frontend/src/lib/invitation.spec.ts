import { describe, expect, it } from 'vitest';

import {
  desenlaceDelAlta,
  estadoDelRegistro,
  leerToken,
  LONGITUD_MINIMA_CONTRASENA,
} from './invitation';

/**
 * #550. La página de alta no se puede testear —no hay jsdom ni testing-library en este repo— así
 * que lo que estos casos fijan es todo lo que hay: qué pantalla toca y qué se le ofrece a alguien
 * cuando el alta falla. Los dos casos que de verdad justifican el fichero son el 409 y el 502, que
 * desde fuera se parecen (los dos son «no se creó la cuenta») y piden lo contrario el uno del otro.
 */
describe('leerToken (#550)', () => {
  it('saca el token del enlace que manda el correo', () => {
    expect(leerToken('?token=abc123')).toBe('abc123');
  });

  it('sin token y con token vacío son lo mismo: no hay nada que consultar', () => {
    expect(leerToken('')).toBeNull();
    expect(leerToken('?otra=cosa')).toBeNull();
    expect(leerToken('?token=')).toBeNull();
    expect(leerToken('?token=%20%20')).toBeNull();
  });

  it('recorta los espacios que trae un enlace copiado a mano', () => {
    // Un token con un espacio detrás no da error: da un 200 `desconocida` que nadie sabe explicar.
    expect(leerToken('?token=%20abc123%20')).toBe('abc123');
  });
});

describe('estadoDelRegistro (#550)', () => {
  const BASE = {
    ready: true,
    invitesEnabled: true,
    token: 'abc',
    vista: undefined,
    errorConsulta: false,
  } as const;

  it('mientras la config viaja por red no se afirma nada', () => {
    expect(estadoDelRegistro({ ...BASE, ready: false })).toBe('cargando');
    // Y tampoco cuando ya se sabe que hay registro pero el token aún no ha contestado.
    expect(estadoDelRegistro(BASE)).toBe('cargando');
  });

  it('el entorno apagado gana al token, porque allí el token no se puede consultar', () => {
    // Es el estado que se ve en `dev`, cuyo overlay borra las `KEYCLOAK_*` a propósito: los dos
    // endpoints dan 503 sea cual sea el token, así que preguntar solo enseñaría un error peor.
    expect(estadoDelRegistro({ ...BASE, invitesEnabled: false })).toBe('apagado');
    expect(estadoDelRegistro({ ...BASE, invitesEnabled: false, token: null })).toBe('apagado');
  });

  it('entrar a /registro sin token es una pantalla, no un error', () => {
    expect(estadoDelRegistro({ ...BASE, token: null })).toBe('sin-token');
  });

  it('si la consulta del token falla no se queda girando: el 200 es el contrato, un error es de red', () => {
    expect(estadoDelRegistro({ ...BASE, errorConsulta: true })).toBe('error-consulta');
  });

  it('con token resuelto, el estado es el que dice el servidor', () => {
    expect(estadoDelRegistro({ ...BASE, vista: { status: 'valida', email: 'ana@example.com' } })).toBe('valida');
    expect(estadoDelRegistro({ ...BASE, vista: { status: 'caducada' } })).toBe('caducada');
    expect(estadoDelRegistro({ ...BASE, vista: { status: 'canjeada' } })).toBe('canjeada');
    // La revocada llega colapsada en `desconocida` desde el backend, y aquí no se desdobla.
    expect(estadoDelRegistro({ ...BASE, vista: { status: 'desconocida' } })).toBe('desconocida');
  });
});

describe('desenlaceDelAlta (#550)', () => {
  it('el 409 NO ofrece reintento: el servidor cierra la invitación al contestarlo', () => {
    // `accept()` llama a `marcarAceptada()` antes de lanzar el 409, así que el mismo enlace ya solo
    // puede dar 410. Un botón de «vuelve a intentarlo» aquí no puede funcionar nunca.
    const d = desenlaceDelAlta(409);
    expect(d.permiteReintento).toBe(false);
    expect(d.llevaAAcceso).toBe(true);
  });

  it('el 502 SÍ lo ofrece: la invitación sigue viva y el reintento puede dar 201', () => {
    const d = desenlaceDelAlta(502);
    expect(d.permiteReintento).toBe(true);
    expect(d.llevaAAcceso).toBe(false);
  });

  it('el 410 no distingue por qué el token murió, y el texto tampoco lo finge', () => {
    const d = desenlaceDelAlta(410);
    expect(d.permiteReintento).toBe(false);
    expect(d.texto).toMatch(/caducado/);
    expect(d.texto).toMatch(/retirado/);
  });

  it('el 503 no culpa al usuario ni le manda a una pantalla que tampoco funcionará', () => {
    const d = desenlaceDelAlta(503);
    expect(d.permiteReintento).toBe(false);
    expect(d.llevaAAcceso).toBe(false);
  });

  it('lo no previsto se cuenta con el mensaje del servidor, que es más concreto que el nuestro', () => {
    const d = desenlaceDelAlta(400, 'password must be longer than or equal to 12 characters');
    expect(d.texto).toBe('password must be longer than or equal to 12 characters');
    // Sin mensaje no se queda en blanco.
    expect(desenlaceDelAlta(418).texto).not.toBe('');
  });
});

describe('LONGITUD_MINIMA_CONTRASENA (#550)', () => {
  it('es el mismo suelo que el @MinLength del DTO: si divergen, el usuario se entera al enviar', () => {
    expect(LONGITUD_MINIMA_CONTRASENA).toBe(12);
  });
});
