# graphify — cluster deployment handover

> ## ✅ LIVE as of 2026-06-25
>
> `graphify` is deployed and healthy at `https://graphify.downloadserver.co.uk/mcp`
> (streamable-http MCP, bearer-gated). Smoke test: unauth → 401, bearer →
> 200 + valid `initialize` (graphify 1.28.0); `graph_stats` returns the scout
> graph (1129 nodes, 3309 edges, 53 communities). Wired into Claude Code as
> the `graphify` MCP server in `~/.claude.json` (active next session).
>
> **What shipped differed from the plan below** — read these deltas:
> - **Pilot graph = `rhodium-org/scout`** (not the cluster repo). It's an
>   answer-machine for Scout's codebase so we can work on scout faster.
> - **Source delivery = GitHub clone, not Forgejo.** init-container
>   `clone-scout` (alpine/git) shallow-clones the private repo with
>   `ORG_REPO_TOKEN` (mounted as `graphify-secrets/clone-token`).
> - **Graph built in-cluster, not committed.** A second init-container
>   `extract` runs `python -m graphify extract` (tree-sitter AST) and writes
>   `graph.json` to a memory-backed emptyDir. Honors the hard-gate: artifacts
>   are produced by the deployed service, never hand-run + committed. Refresh
>   = restart the pod (re-clone + re-extract at HEAD).
> - **AST-only, no LLM.** graphify treats `.md/.mdx/.qmd/.txt/.rst/.html/`
>   `.yaml/.yml` as *semantic* files that REQUIRE an LLM backend (see
>   `detect.DOC_EXTENSIONS`). Scout's markdown specs + Quarkus YAML tripped
>   `error: no LLM API key found`. We `--exclude` every doc extension to keep
>   extraction key-free. Wiring an LLM (claude-openai-proxy) was rejected for
>   v1: its ~50-min OAuth token would crash pod-init whenever stale — too
>   fragile for a foundational tool. Docs can be layered in later behind an LLM.
> - **Image:** `ghcr.io/rhodium-org/graphify` built by in-repo
>   `.github/workflows/build.yml` (trivy-gated-build@v1, GITHUB_TOKEN —
>   no cross-org PAT needed after the repo moved to rhodium-org).
> - **Gotcha:** `graphify extract --out /data` always nests output under
>   `/data/graphify-out/`, so the server + init healthcheck reference
>   `/data/graphify-out/graph.json` (not `/data/graph.json`).
> - **Deploy gotcha hit:** a bad init spec crashloops → Flux Kustomization
>   health-check blocks 5m → next fix can't apply until timeout (deadlock).
>   Self-heals once a good spec lands and the pod goes healthy.
>
> Original drafting notes below are retained for history.

---

Picking this up: we're standing up `safishamsi/graphify` (a knowledge-graph
MCP server) as an in-cluster service at `graphify.downloadserver.co.uk`.
Cluster manifests are drafted; the image and graph file still need to be
produced before Flux can reconcile cleanly.

## State at handover (2026-06-25)

- **Fork cloned:** `/home/henry/Projects/01-applications/graphify`, origin
  `https://github.com/rhodium-org/graphify` (transferred from `rhodium289`
  2026-06-25), upstream `https://github.com/safishamsi/graphify`. Default
  branch `v8` is fast-forwarded to upstream (39 commits, pushed to fork).
- **Cluster manifests drafted in `00-infrastructure/cluster/`** following the
  forgejo-mcp pattern (sister-Ingress, no oauth2-proxy, bearer auth via
  `GRAPHIFY_API_KEY`, ratelimit middleware). Files listed below.
- **Nothing applied yet.** No commit on the cluster repo, no image pushed,
  no graph file built. The Flux Kustomization is wired in — if you push the
  cluster repo as-is, Flux will retry indefinitely on `ImagePullBackOff`
  until the image lands. Either publish the image first, or comment out the
  registration lines until ready.

## What we decided

| | |
|---|---|
| Deployment shape | One process per graph (serve is single-graph). Single Deployment, single replica, `strategy: Recreate`. |
| Auth | `--api-key` / `GRAPHIFY_API_KEY` env. App-enforced; ingress is sister-Ingress (no oauth2-proxy) because MCP clients can't do browser OAuth. Same pattern as `forgejo-mcp`, `oauth-mcp`. |
| Graph file delivery | init-container `git clone` of the source repo from `forgejo-http.forgejo.svc.cluster.local:3000`, copies `graphify-out/graph.json` into a memory-backed emptyDir. App container mounts read-only. |
| Pilot graph | The cluster repo itself (`rhodium-org/cluster`). Eat-own-dog-food: useful answer-machine for "where is X wired" across hundreds of YAML files. |
| Probes | TCP — `graphify.serve` only exposes `/mcp`, no `/health`. |
| Query log | `GRAPHIFY_QUERY_LOG_DISABLE=1` because default `~/.cache/` path is incompatible with `readOnlyRootFilesystem`. |
| Initial image tag | `latest` per cluster `CLAUDE.md` rule (Flux setter rewrites to `ts-*` after first build). |

## Files created / edited in `00-infrastructure/cluster/`

Created:
```
apps/graphify/base/namespace.yaml
apps/graphify/base/serviceaccount.yaml
apps/graphify/base/deployment.yaml          # init-container + serve container
apps/graphify/base/service.yaml
apps/graphify/base/ingress.yaml             # sister-Ingress + ratelimit Middleware
apps/graphify/base/image-automation.yaml    # ghcr.io/rhodium-org/graphify, ts-{10}
apps/graphify/base/kustomization.yaml
apps/graphify/overlays/production/kustomization.yaml
clusters/production/apps/graphify.yaml                       # Flux Kustomization
clusters/production/flux-system/graphify-image-automation.yaml
secrets/templates/graphify-secrets.yaml                      # ${GRAPHIFY_API_KEY_B64}
```

Edited (append-style):
```
secrets/templates/cloudflare-downloadserver-origin-tls.yaml  # +graphify ns
secrets/templates/github-registry-secret.yaml                # +graphify ns
clusters/production/apps/kustomization.yaml                  # + graphify.yaml
clusters/production/flux-system/kustomization.yaml           # + graphify-image-automation.yaml
00-infrastructure/cluster.env/.env                           # + GRAPHIFY_API_KEY=GENERATE:openssl rand -hex 32
```

None of this is committed. Run `git status` in both repos to see the working
tree state.

## Open decisions / blockers

### 1. Where does the image build live? (BLOCKER for Flux)

**RESOLVED (2026-06-25): repo moved to `rhodium-org`.** The fork was
transferred from `github.com/rhodium289/graphify` to
`github.com/rhodium-org/graphify` (GitHub repo transfer; default branch `v8`,
still a fork of `safishamsi/graphify` so manual upstream syncs via the
`upstream` remote keep working). Local `origin` is repointed.

Because the repo now sits in the same GitHub org as the image
(`ghcr.io/rhodium-org/graphify`), the cross-org PAT problem is gone: a
`.github/workflows/build.yml` can publish using the in-repo `GITHUB_TOKEN`
(`packages: write`) — no `GHCR_RHODIUM_ORG_PAT` needed. Manifests already pin
`ghcr.io/rhodium-org/graphify`, so no manifest change.

Remaining work: add `build.yml` mirroring `forgejo-mcp`'s workflow with one
change — `IMAGE_NAME: rhodium-org/graphify`. The upstream Dockerfile
(`graphify/Dockerfile`) is ready to go — single-stage `python:3.12-slim`,
installs `.[mcp]`, non-root uid 10001, port 8080. No changes needed; just CI
to build and push it.

### 2. Graph file bootstrap

`graphify-out/graph.json` doesn't exist in the cluster repo. The init-container
will fail loudly until it does. To produce it:

```bash
uv tool install graphifyy
cd 00-infrastructure/cluster
ANTHROPIC_BASE_URL=https://claude-openai-proxy.downloadserver.co.uk \
ANTHROPIC_API_KEY="<from cluster.env>" \
graphify extract . --backend claude
git add graphify-out/
git commit -m "graphify: bootstrap graph for cluster repo"
git push
```

Code-only corpora need no LLM; the cluster repo has lots of Markdown specs so
the Claude path is the right one. Use `claude-openai-proxy` so we burn the
subscription, not a paid key. Refresh by re-running `graphify extract . --update`.

### 3. Should we land the Flux registration now or after the image?

Currently both registration lines are committed-but-unpushed. Two safe orders:

- **Land image first, then push manifests.** Cleanest.
- **Push manifests now with the two `clusters/production/...` lines commented
  out.** Stages the manifests but leaves Flux dormant until you flip the
  comments.

If you push as-is, Flux's `graphify` Kustomization will fail health checks
on `ImagePullBackOff` for `ghcr.io/rhodium-org/graphify:latest` (image
doesn't exist yet). Not catastrophic — it just retries — but noisy and
breaks the cluster's "all kustomizations healthy" baseline.

## Bring-up runbook (when you're ready)

1. Pick image option (a/b/c). If (a):
   - On the rhodium289/graphify fork, add `.github/workflows/build.yml`
     copying from another standalone repo (`forgejo-mcp` is the closest fit).
     Change `IMAGE_NAME` to `rhodium-org/graphify`, add a `GHCR_RHODIUM_ORG_PAT`
     secret with `write:packages` on `rhodium-org`, use it in the `docker/login`
     step.
   - Push to `v8`; verify the workflow produces a `latest` + `ts-*` tag at
     `ghcr.io/rhodium-org/graphify`.
2. In the cluster checkout, run `./scripts/generate-secrets.sh` (will:
   GENERATE `GRAPHIFY_API_KEY`, write `graphify-secrets`, add the new ns
   entries to `cloudflare-origin-tls` and `github-registry-secret`).
3. Bootstrap the graph file (section §2 above), commit `graphify-out/`.
4. `git push` the cluster repo. Flux picks up `clusters/production/apps/
   graphify.yaml`, applies the namespace + manifests, reconciles.
5. Smoke test:
   ```bash
   kubectl -n graphify get pods           # init container + server, both Ready
   kubectl -n graphify logs deploy/graphify  # "graph.json staged (… bytes)"
   GRAPHIFY_API_KEY=$(awk -F= '/^GRAPHIFY_API_KEY=/{print $2}' ../cluster.env/.env)
   curl -sS -H "Authorization: Bearer $GRAPHIFY_API_KEY" \
     https://graphify.downloadserver.co.uk/mcp \
     -X POST -H 'content-type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq .
   ```
6. Wire a client. Easiest: add an MCP entry to `~/.claude.json` pointing at
   `https://graphify.downloadserver.co.uk/mcp` with the bearer.

## Useful pointers

- Reference pattern: `apps/forgejo-mcp/` and `apps/oauth-mcp/` — both
  bearer-protected MCPs with sister-Ingress.
- Upstream Dockerfile: `01-applications/graphify/Dockerfile` — keep as-is.
- Upstream HTTP server code: `01-applications/graphify/graphify/serve.py`
  (`serve_http`, `_build_http_app`). `_ApiKeyMiddleware` reads bearer from
  either `Authorization: Bearer …` or `X-API-Key`.
- README sections worth re-reading: "Shared HTTP server" and "Environment
  variables".

## Anti-goals

- **Don't** put graphify behind oauth2-proxy. Sister-Ingress is deliberate
  (MCP clients can't do browser OAuth, and oauth2-proxy clobbers the
  Authorization header anyway).
- **Don't** add a `--api-key` CLI flag to the args list — use the env var
  so the secret never appears in a `kubectl describe`.
- **Don't** try to make one Deployment serve multiple repos' graphs.
  `graphify.serve` is single-file. Either run one Deployment per repo or
  build a merged global graph (`graphify merge-graphs` / `graphify global`)
  and serve that.
