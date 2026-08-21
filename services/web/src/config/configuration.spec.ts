import { describe, expect, it } from 'vitest';

import { isAuthConfigured, isInvitesConfigured, validateEnv, validateJobEnv } from './configuration';

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

describe('entorno sin Keycloak', () => {
  const sinAuth = { NODE_ENV: 'production', DATABASE_URL: base.DATABASE_URL };

  it('arranca sin ninguna KEYCLOAK_* (dev no se conecta a Keycloak)', () => {
    // Regresión: antes esto lanzaba "Falta la variable de entorno requerida", lo que impedía
    // desplegar un entorno que simplemente no usa auth.
    expect(() => validateEnv(sinAuth)).not.toThrow();
    const cfg = validateEnv(sinAuth);
    expect(cfg.KEYCLOAK_ISSUER_URL).toBe('');
    expect(cfg.KEYCLOAK_CLIENT_ID).toBe('');
  });

  it('isAuthConfigured distingue el entorno con y sin issuer', () => {
    expect(isAuthConfigured(sinAuth)).toBe(false);
    expect(isAuthConfigured({ KEYCLOAK_ISSUER_URL: '   ' })).toBe(false);
    expect(isAuthConfigured(base)).toBe(true);
  });
});

describe('entorno sin correo saliente', () => {
  it('las tres del correo son opcionales y quedan vacías (es lo que corre en dev)', () => {
    const cfg = validateEnv(base);
    expect(cfg.RESEND_API_KEY).toBe('');
    expect(cfg.INVITE_FROM_EMAIL).toBe('');
    expect(cfg.APP_PUBLIC_URL).toBe('');
  });

  it('recorta y quita la barra final de APP_PUBLIC_URL, que se concatena con la ruta del alta', () => {
    const cfg = validateEnv({ ...base, APP_PUBLIC_URL: '  https://dealtracker.example/  ' });
    expect(cfg.APP_PUBLIC_URL).toBe('https://dealtracker.example');
  });

  it('el client de administración cae en deal-tracker-api, y su secreto no tiene defecto', () => {
    const cfg = validateEnv(base);
    expect(cfg.KEYCLOAK_ADMIN_CLIENT_ID).toBe('deal-tracker-api');
    expect(cfg.KEYCLOAK_ADMIN_CLIENT_SECRET).toBe('');
  });

  it('el remitente y la URL pública son independientes: en qa ni comparten dominio', () => {
    const cfg = validateEnv({
      ...base,
      INVITE_FROM_EMAIL: 'deal-tracker@qa.liontechsolution.com',
      APP_PUBLIC_URL: 'https://dealtracker-qa.liontechsolution.com',
    });
    expect(cfg.INVITE_FROM_EMAIL).toBe('deal-tracker@qa.liontechsolution.com');
    expect(cfg.APP_PUBLIC_URL).toBe('https://dealtracker-qa.liontechsolution.com');
  });
});

describe('isInvitesConfigured · el tercer interruptor', () => {
  const completo = {
    ...base,
    KEYCLOAK_ADMIN_CLIENT_SECRET: 'sh',
    RESEND_API_KEY: 're_123',
  };

  it('con las tres piezas, el registro está encendido', () => {
    expect(isInvitesConfigured(completo)).toBe(true);
  });

  it('la auth puede estar puesta y el registro no: por eso no basta con isAuthConfigured', () => {
    // Es el caso que justifica que exista este interruptor aparte, y el que corre en qa/prod
    // mientras los secretos no estén sellados (llegan al pod con `optional: true`).
    const sinSecreto = { ...completo, KEYCLOAK_ADMIN_CLIENT_SECRET: '' };
    expect(isAuthConfigured(sinSecreto)).toBe(true);
    expect(isInvitesConfigured(sinSecreto)).toBe(false);
  });

  it('sin correo tampoco: descontar el cupo y no mandar la invitación es peor que no invitar', () => {
    expect(isInvitesConfigured({ ...completo, RESEND_API_KEY: '' })).toBe(false);
    expect(isInvitesConfigured({ ...completo, RESEND_API_KEY: '   ' })).toBe(false);
  });

  it('sin auth está apagado aunque sobren secretos: es lo que pasa en dev por construcción', () => {
    const { KEYCLOAK_ISSUER_URL: _issuer, ...sinAuth } = completo;
    expect(isInvitesConfigured(sinAuth)).toBe(false);
  });
});
