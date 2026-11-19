# infrastructure

Everything Docker / deployment / ops. Build context for Docker images is the repo
root (so `COPY backend/...` works), and compose is layered per environment.

| Path | Contents |
|---|---|
| `compose/compose.yml` | The local stack (postgres:17, redis:8, optional `app`/`worker` via `--profile full`) |
| `docker/django/Dockerfile` | The application image (multi-stage; `COPY backend/...`) |
| `docker/nginx/nginx.conf` | Reverse-proxy configuration |
| `ops/alerts/` | Prometheus alert rules + the local alert sink (`local_sink.py`) |

## Run

```sh
# from the repo root:
docker compose -f infrastructure/compose/compose.yml up -d postgres redis   # data services
docker compose -f infrastructure/compose/compose.yml --profile full up      # + app/worker
```
