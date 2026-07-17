# deal-tracker

Plataforma que **rastrea automáticamente ofertas** de ropa y calzado **barefoot para niños** y avisa a las familias, vía **bot de Telegram**, cuando una prenda que les interesa tiene una rebaja significativa.

## Motivación

Facilitar la vida a los padres que buscan ropa para sus hijos pero no llegan a fin de mes. En lugar de vigilar manualmente muchas tiendas, el usuario configura en la plataforma qué prendas le interesan y el sistema hace el seguimiento de precios por él, avisándole solo cuando aparece una oferta que merece la pena.

## Funcionalidades clave

- **Seguimiento de intereses por usuario:** cada familia configura, desde la plataforma web, en qué prendas está interesada para que se les haga el seguimiento de ofertas.
- **Aviso por Telegram:** notificación cuando una prenda seguida baja de precio de forma significativa.
- **Filtrado por talla y por modelo/color:** según lo que permita cada web, ya que el precio puede variar según la variante elegida.
- **Segmentación niño / niña.**
- **Secciones claramente diferenciadas:** **Ropa** y, aparte, **Zapatería** (calzado).
- **Categorías de ropa (al menos 5):** pantalones, camisetas, sudaderas/jerseys, vestidos y ropa interior.
- **Detección de altas y bajas de catálogo:** el sistema debe ser sensible a productos nuevos y a productos descatalogados, para dejar de consultar los que ya no existen. Requiere encontrar un **identificador único de producto por tienda**.
- **Historial de precios:** se almacenan los precios a lo largo del tiempo para, más adelante, generar gráficos de evolución y **detectar descuentos engañosos** (cuando el porcentaje de rebaja anunciado no es real).

## Obtención de precios

La vía prevista es el **web scraping**, pero la mejor forma de monitorizar precios **depende de cada tienda** y habrá que investigarlo sobre la marcha, sorteando los obstáculos anti-scraping que aparezcan. Por eso los scrapers se diseñarán **por tienda y de forma desacoplada (pluggable)**.

### Tiendas objetivo

- Mango Kids
- Sfera
- H&M
- Springfield Kids
- Zara
- C&A
- Hipercor
- Lefties

## Arquitectura (borrador)

> Decisión abierta: **monolito vs. microservicios**. Se decidirá al arrancar el código.

Piezas previstas:

- **Plataforma web + autenticación:** donde los usuarios configuran sus prendas de interés.
- **Servicio(s) de scraping:** un scraper por tienda, sujeto a investigación por las particularidades de cada web.
- **Jobs / cronjobs de refresco:** procesos que rastrean y actualizan las ofertas cada cierto tiempo.
- **Base de datos con historial de precios:** almacena precios en el tiempo para gráficas y detección de descuentos falsos.
- **Bot de Telegram:** canal de notificación de ofertas.

## Infraestructura

- **Cluster k3s** como destino de despliegue.
- **PostgreSQL en HA** ya montado en el cluster; se usará salvo que haya una razón fuerte para otra base de datos.
- **Keycloak** ya desplegado en el cluster, para el sistema de login.
- **Entornos:** `dev local` (dispositivo local), `dev` (cluster), `qa` (cluster) y `prod` (cluster).
- **CI:** GitHub Actions.
- **Despliegue:** ArgoCD, ya montado en k3s.
- **Acceso al cluster:** kubeconfig en `~/.kube/k3slocal.yaml` (para inspeccionar el cluster cuando haga falta contexto).

### Organización de repositorios

- **Este repo** (bajo la organización de GitHub **`liontechsolution`**): construye la aplicación y el **artifact/imagen** que se despliega en el cluster. Aquí vive el **`Dockerfile`**, que debe estar muy optimizado.
- **Repo de manifiestos** (`k3s-local-manifest` o similar, bajo el usuario `juanjocop`): define lo desplegado en el cluster (manifiestos de Kubernetes).

## Estado del proyecto

Fase inicial (greenfield): todavía **no hay código de aplicación**. Este repo arranca con la documentación de contexto para empezar a construir en la siguiente sesión.

Decisiones pendientes para la próxima sesión:

- Monolito vs. microservicios y stack tecnológico concreto.
- Esquema de datos (catálogo, variantes talla/color, historial de precios, intereses de usuario).
- Estrategia del **identificador único de producto por tienda** (base de la detección de altas/bajas).

## Contexto para asistentes de IA

El archivo [`CLAUDE.md`](./CLAUDE.md) recoge la guía de contexto del proyecto para Claude Code y se sincroniza vía este repo, de modo que esté disponible desde cualquier dispositivo donde se clone.
