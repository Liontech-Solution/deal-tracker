import { describe, expect, it } from 'vitest';

import { renderInvitationEmail } from './invitation.template';

/**
 * El cuerpo del correo es de lo poco de esta versión que se puede probar sin gastar un envío real,
 * así que se prueba a fondo aquí: que el enlace viaja intacto, que la caducidad se pinta en la zona
 * del usuario y no en la del pod, y que el nombre de quien invita —dato de usuario— se escapa.
 */

const DATOS = {
  inviterName: 'Juanjo',
  url: 'https://dealtracker-qa.liontechsolution.com/registro?token=abc123',
  // 23:30 UTC del 27: en Europe/Madrid ya es el 28. Es justo el caso que el pod en UTC pintaría mal.
  expiresAt: new Date('2026-08-27T23:30:00Z'),
};

describe('renderInvitationEmail', () => {
  it('lleva quién invita, el enlace y la caducidad', () => {
    const { subject, html, text } = renderInvitationEmail(DATOS);

    expect(subject).toBe('Juanjo te invita a Deal Tracker');
    for (const cuerpo of [html, text]) {
      expect(cuerpo).toContain('Juanjo');
      expect(cuerpo).toContain(DATOS.url);
      expect(cuerpo).toContain('28 de agosto de 2026');
    }
  });

  it('el enlace va también en claro, para cuando el botón no funciona', () => {
    const { html } = renderInvitationEmail(DATOS);

    expect(html).toContain(`href="${DATOS.url}"`);
    // Dos apariciones: el botón y el enlace copiable de debajo.
    expect(html.split(DATOS.url).length - 1).toBe(2);
  });

  it('escapa el nombre de quien invita en el HTML', () => {
    const { html, subject } = renderInvitationEmail({
      ...DATOS,
      inviterName: '<script>alert("x")</script>',
    });

    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
    // El asunto no es HTML: ahí el nombre va tal cual, y escaparlo sería lo que se vería mal.
    expect(subject).toContain('<script>');
  });

  it('escapa también el enlace, que lleva un token de fuera', () => {
    const { html } = renderInvitationEmail({
      ...DATOS,
      url: 'https://x.example/registro?token=a&b="c"',
    });

    expect(html).toContain('href="https://x.example/registro?token=a&amp;b=&quot;c&quot;"');
  });

  it('sin nombre de quien invita, no deja el hueco vacío', () => {
    const { subject, text } = renderInvitationEmail({ ...DATOS, inviterName: '   ' });

    expect(subject).toBe('Alguien te invita a Deal Tracker');
    expect(text).toContain('Alguien te invita');
  });

  it('el enlace no depende del remitente: en qa ni comparten dominio', () => {
    // `renderInvitationEmail` no recibe el `from` —ni podría mirarlo— y el enlace viaja tal cual
    // aunque su host no tenga nada que ver con el dominio desde el que se envía, que es
    // exactamente el caso de qa (`deal-tracker@qa.…` enviando enlaces a `dealtracker-qa.…`).
    const { html, text } = renderInvitationEmail({ ...DATOS, url: 'https://otro.example/registro?token=z' });

    for (const cuerpo of [html, text]) {
      expect(cuerpo).toContain('https://otro.example/registro?token=z');
      expect(cuerpo).not.toContain('@');
    }
  });
});
