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

* **CI/CD.** There is no pipeline that builds, tests and deploys to a staging server and then to
  production with an approval step. Deployment is the manual steps above.
* **A staging environment.** Only one stack is described.
* **An automated off-server copy of the backups.** The script makes the dump; copying it away is
  up to you (cron plus `rsync` or an object-storage client).
* **A rollback rehearsal on the live server.** The steps above are written and the restore is
  tested on the development database, but a full rollback has not been rehearsed on a server.
