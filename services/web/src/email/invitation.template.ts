/**
 * El correo de la invitación: HTML plano y en español, sin motor de plantillas.
 *
 * Vive **fuera del cliente** a propósito: `EmailApiClient` no sabe de invitaciones, para que el
 * segundo correo que haya lo reutilice tal cual (#547). Y vive aquí y no en el servicio que lo
 * manda (#549) para que el cuerpo del correo tenga specs propias, que es lo que no se puede probar
 * en QA sin gastar un envío real.
 *
 * ── EL REMITENTE Y EL ENLACE NO COMPARTEN DOMINIO, Y NO ES UN DESPISTE ──
 *
 * Los dos salen del entorno (`INVITE_FROM_EMAIL` y `APP_PUBLIC_URL`, que pone el ConfigMap de cada
 * overlay: nada de esto se hornea en la imagen), y en QA **ni siquiera comparten dominio** — el de
 * correo lo comparten todos los entornos de QA del cluster, para no gastar un dominio del cupo de
 * Resend por servicio, mientras que el de la SPA es propio. En prod sí coinciden, y esa coincidencia
 * es lo que invita a confundirlos. Son **dos variables independientes** y ninguna se deriva de la
 * otra: esta plantilla recibe la URL ya armada y no mira el remitente para nada.
 */

/** Lo que hay que saber para escribir el correo. La URL llega ya armada, con su token dentro. */
export interface InvitationEmailData {
  /** Quién invita, tal como lo verá el invitado. Es dato de usuario: se escapa. */
  inviterName: string;
  /** Enlace completo al alta, `APP_PUBLIC_URL` incluido (lo arma #549). */
  url: string;
  /** Cuándo deja de valer el enlace. La política son 7 días y la fija quien crea la invitación. */
  expiresAt: Date;
}

/** Un correo listo para `EmailApiClient.sendEmail()`, sin destinatario: ese lo pone quien envía. */
export interface RenderedEmail {
  subject: string;
  html: string;
  text: string;
}

/**
 * Formato de la caducidad, en la zona del usuario y no en la del pod: el contenedor corre en UTC y
 * «caduca el 27 a las 23:30» se leería con un día de menos media hora al año. Sin hora, porque la
 * ventana son días y una hora exacta sugiere una precisión que no aporta nada.
 */
const CADUCIDAD = new Intl.DateTimeFormat('es-ES', {
  dateStyle: 'long',
  timeZone: 'Europe/Madrid',
});

/** Escapa lo que se interpola en el HTML. Todo lo que entra aquí viene de fuera. */
function escapar(texto: string): string {
  return texto
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Arma el correo de invitación. Lleva las tres cosas que el invitado necesita: **quién le invita**
 * (sin eso, un correo con un enlace es indistinguible de un phishing), el enlace, y hasta cuándo
 * vale.
 */
export function renderInvitationEmail(data: InvitationEmailData): RenderedEmail {
  const quien = data.inviterName.trim() || 'Alguien';
  const caduca = CADUCIDAD.format(data.expiresAt);
  const quienHtml = escapar(quien);
  const urlHtml = escapar(data.url);

  const html = `<!doctype html>
<html lang="es">
  <body style="margin:0;padding:24px;background:#f6f6f6;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;color:#1a1a1a;">
    <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:32px;">
      <h1 style="margin:0 0 16px;font-size:20px;">Te han invitado a Deal Tracker</h1>
      <p style="margin:0 0 16px;line-height:1.5;">
        ${quienHtml} te invita a Deal Tracker, donde se siguen las ofertas de ropa y calzado
        barefoot infantil y se avisa por Telegram cuando bajan de precio.
      </p>
      <p style="margin:0 0 24px;line-height:1.5;">
        Para crear tu cuenta, entra aquí:
      </p>
      <p style="margin:0 0 24px;">
        <a href="${urlHtml}" style="display:inline-block;padding:12px 20px;background:#1a7f5a;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600;">Crear mi cuenta</a>
      </p>
      <p style="margin:0 0 8px;line-height:1.5;font-size:14px;color:#555555;">
        La invitación caduca el ${caduca}. Si el botón no funciona, copia este enlace:
      </p>
      <p style="margin:0 0 24px;font-size:13px;color:#555555;word-break:break-all;">${urlHtml}</p>
      <p style="margin:0;font-size:13px;color:#777777;line-height:1.5;">
        Si no esperabas esta invitación, ignora este correo: sin abrir el enlace no se crea ninguna cuenta.
      </p>
    </div>
  </body>
</html>`;

  // Alternativa en texto plano. Es barata, y un correo que solo lleva HTML puntúa peor en los
  // filtros — que es justo lo que #556 tendrá que medir.
  const text = [
    'Te han invitado a Deal Tracker',
    '',
    `${quien} te invita a Deal Tracker, donde se siguen las ofertas de ropa y calzado barefoot`,
    'infantil y se avisa por Telegram cuando bajan de precio.',
    '',
    'Para crear tu cuenta, entra aquí:',
    data.url,
    '',
    `La invitación caduca el ${caduca}.`,
    '',
    'Si no esperabas esta invitación, ignora este correo: sin abrir el enlace no se crea ninguna cuenta.',
  ].join('\n');

  return { subject: `${quien} te invita a Deal Tracker`, html, text };
}
