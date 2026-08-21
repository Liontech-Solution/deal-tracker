import { describe, expect, it } from 'vitest';

import { buildPublicConfig, splitIssuer } from './public-config';

const ISSUER = 'https://keycloak-dev.liontechsolution.com/realms/deal-tracker-dev';

describe('buildPublicConfig', () => {
  it('parte el issuer en url + realm', () => {
    expect(buildPublicConfig(ISSUER, 'deal-tracker-web')).toEqual({
      url: 'https://keycloak-dev.liontechsolution.com',
      realm: 'deal-tracker-dev',
      clientId: 'deal-tracker-web',
      invitesEnabled: false,
    });
  });

  it('tolera barra final y espacios', () => {
    expect(buildPublicConfig(`  ${ISSUER}/  `, '  deal-tracker-web  ')).toEqual({
      url: 'https://keycloak-dev.liontechsolution.com',
      realm: 'deal-tracker-dev',
      clientId: 'deal-tracker-web',
      invitesEnabled: false,
    });
  });

  it('soporta Keycloak servido bajo un sub-path (/auth)', () => {
    expect(buildPublicConfig('https://kc.example/auth/realms/mi-realm', 'spa')).toEqual({
      url: 'https://kc.example/auth',
      realm: 'mi-realm',
      clientId: 'spa',
      invitesEnabled: false,
    });
  });

  const disabled = { url: null, realm: null, clientId: null, invitesEnabled: false };

  it('deshabilita la auth si falta el issuer', () => {
    expect(buildPublicConfig('', 'deal-tracker-web')).toEqual(disabled);
    expect(buildPublicConfig('   ', 'deal-tracker-web')).toEqual(disabled);
  });

  it('deshabilita la auth si falta el client-id', () => {
    expect(buildPublicConfig(ISSUER, '')).toEqual(disabled);
  });

  it('deshabilita la auth si el issuer no es una URL de realm', () => {
    // Sin `/realms/`, con el realm vacío, con path extra detrás del realm, o sin base.
    expect(buildPublicConfig('https://kc.example', 'spa')).toEqual(disabled);
    expect(buildPublicConfig('https://kc.example/realms/', 'spa')).toEqual(disabled);
    expect(buildPublicConfig('https://kc.example/realms/mi-realm/extra', 'spa')).toEqual(disabled);
    expect(buildPublicConfig('/realms/mi-realm', 'spa')).toEqual(disabled);
  });

  it('publica invitesEnabled cuando el entorno puede dar altas', () => {
    expect(buildPublicConfig(ISSUER, 'deal-tracker-web', true).invitesEnabled).toBe(true);
  });

  it('fuerza invitesEnabled a false si la auth queda deshabilitada, aunque le digan que sí', () => {
    // No es teórico: `isInvitesConfigured()` solo mira que el issuer no esté VACÍO, así que un
    // issuer mal formado con los dos secretos puestos llega aquí con `true`. Publicarlo sería
    // prometer un alta que la SPA no podría completar, porque no sabría autenticarse después.
    expect(buildPublicConfig('https://kc.example/sin-realms', 'spa', true)).toEqual(disabled);
    expect(buildPublicConfig(ISSUER, '', true)).toEqual(disabled);
  });
});

describe('splitIssuer', () => {
  it('devuelve base y realm, que es lo que la Admin API necesita para armar sus rutas', () => {
    expect(splitIssuer(ISSUER)).toEqual({
      url: 'https://keycloak-dev.liontechsolution.com',
      realm: 'deal-tracker-dev',
    });
  });

  it('devuelve null cuando no hay realm que administrar', () => {
    expect(splitIssuer('')).toBeNull();
    expect(splitIssuer('https://kc.example')).toBeNull();
    expect(splitIssuer('https://kc.example/realms/mi-realm/extra')).toBeNull();
  });
});
