import { describe, expect, it } from 'vitest';

import { buildPublicConfig } from './public-config';

const ISSUER = 'https://keycloak-dev.liontechsolution.com/realms/deal-tracker-dev';

describe('buildPublicConfig', () => {
  it('parte el issuer en url + realm', () => {
    expect(buildPublicConfig(ISSUER, 'deal-tracker-web')).toEqual({
      url: 'https://keycloak-dev.liontechsolution.com',
      realm: 'deal-tracker-dev',
      clientId: 'deal-tracker-web',
    });
  });

  it('tolera barra final y espacios', () => {
    expect(buildPublicConfig(`  ${ISSUER}/  `, '  deal-tracker-web  ')).toEqual({
      url: 'https://keycloak-dev.liontechsolution.com',
      realm: 'deal-tracker-dev',
      clientId: 'deal-tracker-web',
    });
  });

  it('soporta Keycloak servido bajo un sub-path (/auth)', () => {
    expect(buildPublicConfig('https://kc.example/auth/realms/mi-realm', 'spa')).toEqual({
      url: 'https://kc.example/auth',
      realm: 'mi-realm',
      clientId: 'spa',
    });
  });

  const disabled = { url: null, realm: null, clientId: null };

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
});
