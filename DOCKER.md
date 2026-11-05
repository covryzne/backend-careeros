# Docker Compose backend

Tersedia `compose.development.yml`, `compose.staging.yml`, dan `compose.production.yml`.
Salin template `.env.<environment>.example` menjadi file privat `.env.<environment>` dan isi placeholder.

Panduan lengkap: [DOCKER_COMPOSE.md](../specs-careeros/DOCKER_COMPOSE.md).

```bash
docker compose --env-file .env.development -f compose.development.yml up -d --build
```

Database development berada di host (`host.docker.internal` dari container). Staging/prod memakai Supabase terpisah. Migration dijalankan manual setelah memastikan target database. Dokumen disimpan pada volume persisten.

Backend kini memverifikasi Supabase Auth user JWT dan memvalidasi konfigurasi startup staging/production. Ikuti [STAGING.md](STAGING.md) untuk env, build, migration, seed dan serve di server. Local identity tetap khusus development/test. Frontend login, dukungan worker staging/headed dan verifikasi live tetap pekerjaan terpisah.
