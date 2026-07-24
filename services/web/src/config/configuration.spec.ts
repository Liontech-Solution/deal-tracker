import { describe, expect, it } from 'vitest';

import { validateEnv, validateJobEnv } from './configuration';

const base = {
  NODE_ENV: 'production',
  DATABASE_URL: 'postgresql://u:p@localhost:5432/deal_tracker',
  KEYCLOAK_ISSUER_URL: 'https://kc.example/realms/deal-tracker',
  KEYCLOAK_AUDIENCE: 'deal-tracker-web',
};

describe('validateEnv · client-id de Keycloak', () => {
  it('cae en KEYCLOAK_AUDIENCE cuando no se define KEYCLOAK_CLIENT_ID', () => {
    // Es lo que permite encender el login en QA sin tocar los manifiestos: la audiencia ya
    // vale el propio client-id.
    expect(validateEnv(base).KEYCLOAK_CLIENT_ID).toBe('deal-tracker-web');
  });

  it('respeta KEYCLOAK_CLIENT_ID cuando se define', () => {
    const env = { ...base, KEYCLOAK_CLIENT_ID: '  otra-spa  ' };
    expect(validateEnv(env).KEYCLOAK_CLIENT_ID).toBe('otra-spa');
  });

  it('nunca es requerido: no rompe el arranque si falta', () => {
    expect(() => validateEnv(base)).not.toThrow();
  });

  it('queda vacío en los jobs, que no son resource server', () => {
    const { KEYCLOAK_CLIENT_ID } = validateJobEnv({
      NODE_ENV: 'production',
      DATABASE_URL: base.DATABASE_URL,
    });
    expect(KEYCLOAK_CLIENT_ID).toBe('');
  });
});
