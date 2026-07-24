---
name: memoria-en-repo
description: La memoria vive versionada en el repo y se enlaza por symlink desde ~/.claude
metadata:
  type: project
---

Decisión del 2026-07-24: la memoria de este proyecto se versiona **dentro del repo**, en
`.claude/memory/`, para poder usarla desde los dos equipos (ver [[kubeconfig-location]]).

Claude Code lee la memoria de `~/.claude/projects/<slug>/memory`, donde `<slug>` deriva de la **ruta
absoluta** del repo (aquí `-home-juanjocop-Proyectos-deal-tracker`). Para que apunte al repo se deja
un symlink:

```
~/.claude/projects/-home-juanjocop-Proyectos-deal-tracker/memory -> <repo>/.claude/memory
```

**En cada equipo nuevo hay que crear ese symlink a mano** (una vez): el contenido viaja por git, pero
el enlace no, y si el repo está clonado en otra ruta el `<slug>` será distinto. Sin el symlink, ese
equipo escribiría memoria en una carpeta local y no se compartiría.
