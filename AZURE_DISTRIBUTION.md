# Distribución del fork vía Azure DevOps (Phase 3)

Cómo `tecnoandina-graphify` se construye y distribuye para que `yana graf install`
lo instale. Arquitectura:

```
Azure DevOps Repos (tecnoandinaspa/graphify)  ← espejo de github isma90/graphify@v8
   │  azure-pipelines.yml dispara en tag vX.Y.Z
   ▼
Azure Artifacts (Universal Package "graphify", feed tecnoandinaspa)
   │  yana-server baja el wheel con su PAT (Secret Manager)  [hook futuro]
   ▼
yana-server  GET /catalog/graphify/{manifest,download}  (JWT)
   ▼
yana CLI  `yana graf install`  → pipx install <wheel>
```

> Estado: el `azure-pipelines.yml` ya está en el repo. Los pasos de abajo
> requieren **acceso/permisos en Azure DevOps** y se hacen una sola vez.

## 1. Espejar el repo a Azure DevOps Repos

Crear el repo `graphify` en el proyecto Azure DevOps (org `tecnoandinaspa`) y
espejarlo desde GitHub:

```bash
# Desde un clon del fork de GitHub:
git remote add azure https://tecnoandinaspa@dev.azure.com/tecnoandinaspa/<PROJECT>/_git/graphify
git push azure --all
git push azure --tags
# (alternativa full mirror): git push azure --mirror
```

Sincronización continua (opcional): un cron/job que haga
`git fetch origin && git push azure --all --tags`, o trabajar directamente en
Azure Repos como fuente de verdad.

## 2. Feed de Azure Artifacts

- El pipeline publica un **Universal Package** llamado `graphify` al feed
  `tecnoandinaspa` (ver var `artifactsFeed` en `azure-pipelines.yml`).
- Si se prefiere un feed dedicado, crear `graphify` y actualizar `artifactsFeed`
  a `<PROJECT>/graphify`.
- Dar permiso de **publish** al service account del pipeline y permiso de
  **read** al service account que use yana-server.

## 3. Release

```bash
# Bumpear versión en pyproject.toml, luego:
git tag v0.10.2
git push azure v0.10.2     # dispara el stage `publish`
```

El pipeline corre tests (py3.10/3.12), `python -m build`, calcula sha256
(`dist/SHA256SUMS`) y publica el Universal Package `graphify@0.10.2`.

## 4. Consumo desde yana-server (hook futuro)

Hoy yana-server sirve el wheel **baked** en `catalog/artifacts/`
(`GRAPHIFY_ARTIFACTS_PATH`, `GRAPHIFY_PINNED_VERSION`). Para consumir el feed:

1. Extender `src/azdo/client.ts` con `downloadUniversalPackage(feed, name, version)`
   (REST: `https://pkgs.dev.azure.com/tecnoandinaspa/_apis/packaging/feeds/<feed>/upack/packages/<name>/versions/<version>/content`,
   auth Basic con el PAT de `pat-loader`).
2. En `src/artifacts/graphify-service.ts`, resolver el wheel: primero cache local,
   si falta bajarlo del feed por `GRAPHIFY_PINNED_VERSION` y cachearlo.
3. Bumpear `GRAPHIFY_PINNED_VERSION` (helm/k8s) controla el rollout sin tocar el CLI.

El **contrato HTTP del CLI no cambia** al migrar de baked → feed.
