import { describe, expect, it } from 'vitest';

import {
  desenlaceDelAlta,
  estadoDelRegistro,
  etiquetaDeEstado,
  leerToken,
  LONGITUD_MINIMA_CONTRASENA,
  mensajeDelErrorAlInvitar,
  puedeRevocarse,
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

/**
 * #551, la otra punta. Misma limitación y mismo remedio: la tarjeta de `/ajustes` no se puede
 * renderizar en un test, así que lo que se fija aquí es lo que decide si el usuario tiene salida.
 * El caso que justifica el fichero es el de la caducada: es el único de los cuatro estados donde
 * «ya no sirve» y «no se puede revocar» parecen lo mismo y no lo son.
 */
describe('puedeRevocarse (#551)', () => {
  it('la viva se puede revocar: es lo que devuelve el cupo', () => {
    expect(puedeRevocarse('viva')).toBe(true);
  });

  it('la CADUCADA también, porque sigue ocupando el correo', () => {
    // El predicado de `ux_invitation_email_viva` no puede mirar `expires_at`, así que volver a
    // invitar a ese correo da 409 mientras la fila caducada siga sin cerrar. Revocar es la única
    // salida y no hay job de limpieza: si esto fuese `false`, el correo quedaría bloqueado para
    // siempre.
    expect(puedeRevocarse('caducada')).toBe(true);
  });

  it('la aceptada y la revocada no: su DELETE es un 404', () => {
    expect(puedeRevocarse('canjeada')).toBe(false);
    expect(puedeRevocarse('revocada')).toBe(false);
  });
});

describe('etiquetaDeEstado (#551)', () => {
  it('da rótulo y tono a los cuatro estados', () => {
    expect(etiquetaDeEstado('viva')).toEqual({ texto: 'Pendiente', tono: 'vivo' });
    expect(etiquetaDeEstado('caducada')).toEqual({ texto: 'Caducada', tono: 'neutro' });
    expect(etiquetaDeEstado('canjeada')).toEqual({ texto: 'Aceptada', tono: 'exito' });
    expect(etiquetaDeEstado('revocada')).toEqual({ texto: 'Revocada', tono: 'neutro' });
  });

  it('caducada y revocada comparten tono pero no texto', () => {
    // Las dos son «ya no sirve», pero una se murió sola y la otra la retiró quien invitaba.
    // Fundirlas haría creer que el sistema retira invitaciones por su cuenta.
    expect(etiquetaDeEstado('caducada').tono).toBe(etiquetaDeEstado('revocada').tono);
    expect(etiquetaDeEstado('caducada').texto).not.toBe(etiquetaDeEstado('revocada').texto);
  });
});

describe('mensajeDelErrorAlInvitar (#551)', () => {
  it('el 409 propaga el mensaje del servidor, que es el que dirige a revocar', () => {
    const delServidor =
      'Ese correo ya tiene una invitación pendiente. Revócala en tus ajustes para volver a invitarlo: al revocarla recuperas el cupo, y sirve también si ya ha caducado.';
    expect(mensajeDelErrorAlInvitar(409, delServidor)).toBe(delServidor);
    // Y sin él tampoco se queda sin decir cuál es la salida.
    expect(mensajeDelErrorAlInvitar(409)).toContain('Revócala');
  });

  it('el 502 dice que NO se ha gastado cupo, que es lo que distingue el reintento', () => {
    // El servicio borra la fila y devuelve el cupo. Sin esta frase el usuario ve el mismo número y
    // no sabe si ha perdido una invitación.
    expect(mensajeDelErrorAlInvitar(502)).toContain('No se ha gastado');
  });

  it('el 403 es la política, no un fallo', () => {
    expect(mensajeDelErrorAlInvitar(403)).toBe('No te quedan invitaciones.');
  });

  it('el 503 habla del servidor, no del usuario', () => {
    expect(mensajeDelErrorAlInvitar(503)).toContain('servidor');
  });

  it('el 400 propaga la validación del servidor y nunca se queda en blanco', () => {
    expect(mensajeDelErrorAlInvitar(400, 'email must be an email')).toBe('email must be an email');
    expect(mensajeDelErrorAlInvitar(400)).not.toBe('');
    expect(mensajeDelErrorAlInvitar(418)).not.toBe('');
  });
});
