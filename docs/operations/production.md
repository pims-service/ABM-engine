# Production deployment, backups and rollback

How to run V1 on one Linux server with Docker, and how to back it up, restore it and go back to
the previous version. The files are in [`infra/prod/`](../../infra/prod/). This is the setup the
team's first server uses; it is deliberately small (one machine, one worker). For more
throughput see [scaling](scaling.md).

What runs: Caddy (HTTPS, the only service with published ports), the Next.js `web` service, the
Django `api` (migrations run when it starts), the Django-Q2 `worker`, and PostgreSQL. There is no
Redis.

## Before you start

* A Linux server with Docker and the Compose plugin, ports 80 and 443 open.
* A host name or an IP address people will use (`PUBLIC_HOST`). With a domain name, Caddy gets a
  trusted certificate by itself; remove `tls internal` from `Caddyfile` for that. With a bare IP
  address the certificate is Caddy's own, so browsers warn once until it is trusted.
* Nothing secret is in the repository or the images. Secrets live in the `.env` file on the
  server only.

## First deploy

1. Copy `infra/prod/` to the server (for example to `/opt/abm`) and create the settings file:

   ```bash
   cd /opt/abm
   cp env.example .env && chmod 600 .env
   # edit .env: replace every YOUR_* value; generate SECRET_KEY and
   # INTEGRATION_ENCRYPTION_KEYS with the commands written in the file
   ```

2. Build the two images, both tagged with the same `IMAGE_TAG`. On a slow connection build on
   the server (copy the source there first) instead of uploading images.

   ```bash
   docker build -t abm-api:TAG backend
   docker build -t abm-web:TAG --build-arg NEXT_PUBLIC_API_BASE_URL=https://YOUR_PUBLIC_HOST/api frontend
   # to ship built images instead:  docker save abm-api:TAG abm-web:TAG | gzip | ssh server docker load
   ```

   The web image bakes the public API address in at build time, so build it for the address
   people actually use.

3. Start it (the first start runs the migrations, so wait for `api` to be healthy):

   ```bash
   export COMPOSE_FILE=docker-compose.prod.yml
   docker compose up -d
   docker compose ps
   curl -k https://YOUR_PUBLIC_HOST/readyz
   ```

4. Create the first administrator and keep the password somewhere safe:

   ```bash
   docker compose exec api python manage.py createsuperuser
   ```

## Deploying with the pipeline

`.github/workflows/deploy.yml` (Actions > Deploy > Run workflow) deploys a commit of `main` to a
server without anyone building or uploading an image:

1. **Check the commit.** It must be in `main` and its CI run must have passed.
2. **Build.** GitHub builds the api and web images and pushes them to the GitHub container
   registry (`ghcr.io/<owner>/<repo>/api:<sha>` and `web:<sha>-<environment>`; the web image has the
   public address built in, so it is built per environment).
3. **Deploy over SSH.** It copies `infra/prod/` to the server, logs the server into the registry for
   the length of the run, and runs `deploy.sh`: pull the images, back the database up, switch
   `IMAGE_TAG`, start the stack (the api migrates the database as it starts), wait until the
   database, api, worker and web are healthy and `/readyz` answers, then remove old images (the
   newest three stay).
4. **If the stack is not healthy** within four minutes, `deploy.sh` puts the previous tag back and the
   run fails. The api may already have migrated the database, so it also names the backup to restore
   if the old version cannot use it.

Choose the **environment** (`production` or `staging`), the **action** and the **ref**. The action
`rollback` runs `deploy.sh --rollback`: the server goes back to the tag that ran before the last
deploy (its images are kept). The database is not changed back: restore a backup for that.

### Set it up once

On GitHub (Settings > Environments), create `production` (and `staging` when there is a staging
server). Give `production` **required reviewers**: that is the approval step, asked once per run.
In each environment set:

| Kind | Name | What |
| --- | --- | --- |
| Secret | `DEPLOY_HOST` | the server's address |
| Secret | `DEPLOY_SSH_KEY` | the private half of a key made for deploying only (`ssh-keygen -t ed25519 -f deploy_key`) |
| Secret | `DEPLOY_KNOWN_HOSTS` | the server's host key, from `ssh-keyscan -t ed25519 <host>`; the run refuses a server it does not recognise |
| Variable | `PUBLIC_HOST` | the address people use in the browser (built into the web image) |
| Variable | `DEPLOY_USER`, `DEPLOY_DIR` | optional; default `ec2-user` and `/opt/abm` |

Put the public half of the deploy key in `~/.ssh/authorized_keys` of the deploy user on the server.
The server needs Docker with the Compose plugin, `curl`, and the first-deploy files from "First
deploy" above (`.env` in `DEPLOY_DIR`). The registry login uses the run's own short-lived token,
which is passed over SSH on standard input and removed from the server when the run ends; no
personal access token is stored anywhere. In branch protection for `main`, keep the CI checks
required: the gate only trusts a commit whose CI run passed.

### A staging environment

A staging deploy is the same run with `environment: staging`: it needs a second server (or a
second stack) set up like production, with its own secrets and `PUBLIC_HOST`. Until one exists the
`staging` run stops at the first step and says which secret is missing.

## Deploying by hand

The pipeline is the normal way. By hand, with images already on the server (built there, or loaded
with `docker load`), run `./deploy.sh <tag> --no-pull` in `DEPLOY_DIR`: it does the same backup,
switch, health wait and rollback.

## Updating

Build the new images with a new tag, set `IMAGE_TAG` in `.env` to it, and run `docker compose up -d`.
Compose replaces only the services whose image changed, and the api migrates the database when it
starts. Keep the previous images on the server until the new version has proved itself.

## Backups

`backup.sh` writes a dated, compressed dump of the whole database (companies, research, signals,
decisions, messages, encrypted connection credentials, the job queue) and deletes dumps older than
14 days. It never leaves an empty file behind.

```bash
cd /opt/abm && export COMPOSE_FILE=docker-compose.prod.yml
./backup.sh                      # into ./backups, keeping 14 days
./backup.sh /mnt/backups 30      # another folder, keeping 30 days
```

Schedule it, for example with `crontab -e`:

```
0 3 * * * cd /opt/abm && COMPOSE_FILE=docker-compose.prod.yml ./backup.sh >> backup.log 2>&1
```

A backup that stays on the server is lost with the server. Copy the dumps to storage somewhere
else (object storage or another machine). The integration credentials in the dump are encrypted
with `INTEGRATION_ENCRYPTION_KEYS`, so keep a copy of that value too: without it, the saved API
keys in a restored database cannot be read and have to be entered again.

## Restoring

`restore.sh` restores a dump into a **new** database and refuses to touch one that exists, so a
restore can never overwrite live data by mistake.

```bash
./restore.sh backups/abm-20261101T030000Z.dump             # into abm_restore_test
./restore.sh backups/abm-20261101T030000Z.dump abm_checked  # or a name you choose
```

It prints the number of tables it restored. Check the data (for example with
`docker compose exec db psql -U <user> -d abm_restore_test`). To go live on the restored copy,
stop `api` and `worker`, set `POSTGRES_DB` in `.env` to the new name, and start them again.
Drop a scratch database with `docker compose exec db dropdb -U <user> abm_restore_test`.

Test a restore now and then (monthly is a good habit). A backup you have never restored is a hope,
not a backup. The restore path was tested on the development database: the dump restored into a
fresh database with the same tables and the same row counts.

## Rolling back

* **A bad release, no migration involved:** set `IMAGE_TAG` in `.env` back to the previous tag and
  run `docker compose up -d`. This takes about a minute.
* **A release whose migration changed the database:** the old code may not work against the new
  tables. Take a backup first (`./backup.sh`) before any update that includes a migration, then
  roll back by restoring the pre-update dump with `restore.sh`, pointing `POSTGRES_DB` at the
  restored database and starting the old image tag. Anything entered after the backup is lost, so
  tell people before you do it.

## Checking it is healthy

`/healthz` answers while the api process is up. `/readyz` also checks the database, the
migrations and the worker, and is the one to look at after a deploy. `docker compose ps` shows
each service's health. The worker has its own health check (`worker_healthcheck`).

## What is not here yet

* **The pipeline has not been run.** It was written and checked with actionlint and shellcheck, but
  its first real run needs the environment secrets above. Expect to fix small things then.
* **A staging server.** The workflow supports the environment; there is no server for it.
* **An automated off-server copy of the backups.** The script makes the dump; copying it away is
  up to you (cron plus `rsync` or an object-storage client).
* **A rollback rehearsal on the live server.** The steps are written and the restore is tested on
  the development database, but a full rollback has not been rehearsed on a server.
* **Image scanning.** The images are not scanned for known vulnerabilities in the pipeline.
