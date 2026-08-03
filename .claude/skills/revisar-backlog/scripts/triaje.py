#!/usr/bin/env python3
"""Digest de triaje del backlog: una pasada de `gh` y una tabla de señales.

Lee TODAS las issues abiertas con sus comentarios (una sola llamada a la API) y
emite lo que hace falta para *ordenar*, no para *entender*: quién referencia a
quién, cuántas casillas quedan, y la cola del último comentario. El cuerpo
completo se lee después, y solo el de la lista corta.

Uso:
    python3 .claude/skills/revisar-backlog/scripts/triaje.py [--json ruta.json]

Con --json cachea la respuesta cruda de `gh` en esa ruta y la reutiliza si ya
existe, para no repetir la llamada mientras se itera sobre el mismo backlog.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

CAMPOS = "number,title,body,labels,comments,createdAt,updatedAt,url,author"

# Referencias a otra issue: «#123» pero no «#12345678» de un hash, ni dentro de
# una URL de commit. Nos quedamos con 1-4 dígitos, que cubre este repo de sobra.
REF = re.compile(r"(?<![\w/#])#(\d{1,4})\b")
CASILLA_HECHA = re.compile(r"^\s*[-*]\s*\[[xX]\]", re.M)
CASILLA_PENDIENTE = re.compile(r"^\s*[-*]\s*\[ \]", re.M)
# Una épica se menciona desde casi todo lo que cuelga de ella, así que contarla
# como "la referencian 10 issues" mide pertenencia, no taponamiento.
EPICA = re.compile(r"\b[ée]pica\b", re.I)


def cargar(cache: str | None) -> list[dict]:
    if cache:
        try:
            with open(cache, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            pass
    salida = subprocess.run(
        ["gh", "issue", "list", "--limit", "200", "--state", "open", "--json", CAMPOS],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    datos = json.loads(salida)
    if cache:
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(salida)
    return datos


def dias(iso: str) -> int:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - t).days


def cola(texto: str, chars: int) -> str:
    """Últimos `chars` del texto, en una línea. La conclusión suele estar al final."""
    plano = " ".join((texto or "").split())
    return ("…" + plano[-chars:]) if len(plano) > chars else plano


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="cache", default=None)
    ap.add_argument("--cola", type=int, default=400, help="chars del último comentario")
    args = ap.parse_args()

    issues = sorted(cargar(args.cache), key=lambda i: i["number"])
    abiertas = {i["number"] for i in issues}
    epicas = {i["number"] for i in issues if EPICA.search(i["title"])}

    # Quién menciona a quién. Solo contamos referencias *desde otra issue abierta*
    # hacia esta: es la señal de taponamiento que buscamos.
    entrantes: dict[int, set[int]] = {n: set() for n in abiertas}
    for i in issues:
        texto = (i["body"] or "") + "\n" + "\n".join(c["body"] or "" for c in i["comments"])
        for m in REF.findall(texto):
            destino = int(m)
            if destino in abiertas and destino != i["number"]:
                entrantes[destino].add(i["number"])

    print(f"# Triaje: {len(issues)} issues abiertas")
    if epicas:
        print(f"Épicas detectadas: {', '.join('#' + str(e) for e in sorted(epicas))}")
    print("\nEsto mide, no ordena. `ref<-` = issues abiertas que la mencionan, sin")
    print("contar épicas: colgar de una épica es pertenencia, no dependencia.\n")

    for i in issues:
        n = i["number"]
        cuerpo = i["body"] or ""
        hechas = len(CASILLA_HECHA.findall(cuerpo))
        pend = len(CASILLA_PENDIENTE.findall(cuerpo))
        etiquetas = ",".join(l["name"] for l in i["labels"]) or "-"
        coms = i["comments"]

        print(f"## #{n} — {i['title']}" + ("  [ÉPICA]" if n in epicas else ""))
        print(
            f"- etiquetas: {etiquetas} | creada hace {dias(i['createdAt'])}d, "
            f"tocada hace {dias(i['updatedAt'])}d | cuerpo {len(cuerpo)} chars"
        )
        if hechas or pend:
            print(f"- casillas: {hechas} hechas / {pend} pendientes")
        ref = sorted(entrantes[n] - epicas)
        if ref:
            print(f"- ref<- {len(ref)}: {', '.join('#' + str(r) for r in ref)}")
        salientes = sorted(
            {int(m) for m in REF.findall(cuerpo) if int(m) in abiertas} - {n} - epicas
        )
        if salientes:
            print(f"- menciona a: {', '.join('#' + str(s) for s in salientes)}")
        if coms:
            ultimo = coms[-1]
            fecha = ultimo.get("createdAt", "")[:10]
            print(f"- {len(coms)} comentarios; el último ({fecha}) acaba así:")
            print(f"  > {cola(ultimo['body'], args.cola)}")
        else:
            print("- sin comentarios")
        print()

    print("---")
    print("Lee el cuerpo completo solo de las que vayan a entrar en la lista corta:")
    print("  gh issue view <n> --comments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
