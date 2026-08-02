"""Publicación de hallazgos del vigía como issue de GitHub (#67).

Vive aparte de `vigia.py` por una razón práctica: el vigía **decide** y esto **publica**. Así el
veredicto se puede testear sin tocar la red, y el día que haya un segundo canal (Telegram, que el
proyecto ya tiene bot) entra aquí sin mover la lógica.

**Opcional por diseño**, como Keycloak y Telegram en el servicio web: sin `VIGIA_GITHUB_TOKEN` y
`VIGIA_GITHUB_REPO` el vigía solo imprime. Esa es la ruta de dev local, y significa que un secreto
mal puesto en el cluster degrada a «no avisa» y no a «revienta el job».

**Deduplicación.** El job corre cada semana; una tienda rota lo va a seguir estando la semana que
viene. Sin dedupe, en un mes hay cuatro issues del mismo problema y el vigía se convierte en ruido,
que es la forma en la que estas cosas se acaban silenciando. Por eso: si ya hay una issue abierta
con el marcador en el título, se le añade un comentario; si no, se crea.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

# Marcador en el título con el que se reconoce «la issue del vigía» entre las abiertas. Se busca por
# título y no por etiqueta porque la etiqueta puede no existir en el repo y el título siempre está.
MARCADOR = "[vigía]"
TITULO = f"{MARCADOR} una o más tiendas han dejado de dejarnos entrar"
ETIQUETA = "vigia"
API_POR_DEFECTO = "https://api.github.com"


@dataclass(frozen=True)
class AvisoGitHub:
    """Cliente mínimo de la API de issues. Sin dependencias nuevas: httpx ya está en la imagen."""

    repo: str  # "owner/nombre"
    token: str
    api: str = API_POR_DEFECTO

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AvisoGitHub | None:
        """El aviso configurado, o `None` si no lo está (que es un estado válido, no un error)."""
        env = dict(os.environ) if env is None else env
        token = env.get("VIGIA_GITHUB_TOKEN", "")
        repo = env.get("VIGIA_GITHUB_REPO", "")
        if not token or not repo:
            return None
        return cls(repo=repo, token=token, api=env.get("VIGIA_GITHUB_API", API_POR_DEFECTO))

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.api,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=20.0,
        )

    def publicar(self, cuerpo: str) -> str:
        """Comenta en la issue abierta del vigía, o la crea. Devuelve qué hizo, para el log."""
        with self._client() as client:
            abierta = self._issue_abierta(client)
            if abierta is not None:
                numero = abierta["number"]
                resp = client.post(
                    f"/repos/{self.repo}/issues/{numero}/comments", json={"body": cuerpo}
                )
                resp.raise_for_status()
                return f"comentado en la issue #{numero} ya abierta: {abierta['html_url']}"
            return self._crear(client, cuerpo)

    def _issue_abierta(self, client: httpx.Client) -> dict[str, Any] | None:
        """La issue del vigía que siga abierta, si la hay.

        Se listan las abiertas y se filtra por el marcador del título en vez de filtrar por
        `labels=`: si la etiqueta no existe todavía en el repo, el filtro devolvería vacío y
        crearíamos una issue nueva cada semana, que es justo lo que el dedupe evita.
        """
        resp = client.get(f"/repos/{self.repo}/issues", params={"state": "open", "per_page": 100})
        resp.raise_for_status()
        for issue in resp.json():
            # La API de issues devuelve también los PR; se distinguen por traer `pull_request`.
            if "pull_request" in issue:
                continue
            if MARCADOR in issue.get("title", ""):
                return dict(issue)
        return None

    def _crear(self, client: httpx.Client, cuerpo: str) -> str:
        payload: dict[str, Any] = {"title": TITULO, "body": cuerpo, "labels": [ETIQUETA]}
        resp = client.post(f"/repos/{self.repo}/issues", json=payload)
        if resp.status_code == 422:
            # 422 típico: la etiqueta no existe y el token no puede crearla. El hallazgo importa
            # más que la etiqueta, así que se reintenta sin ella en vez de perder el aviso.
            del payload["labels"]
            resp = client.post(f"/repos/{self.repo}/issues", json=payload)
        resp.raise_for_status()
        creada = resp.json()
        return f"issue #{creada['number']} creada: {creada['html_url']}"
