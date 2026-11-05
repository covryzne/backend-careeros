# Docker Compose backend

Tersedia `compose.development.yml`, `compose.staging.yml`, dan `compose.production.yml`.
Salin template `.env.<environment>.example` menjadi file privat `.env.<environment>` dan isi placeholder.

Panduan lengkap: [DOCKER_COMPOSE.md](../specs-careeros/DOCKER_COMPOSE.md).

```bash
docker compose --env-file .env.development -f compose.development.yml up -d --build
```

Database development berada di host (`host.docker.internal` dari container). Staging/prod memakai Supabase terpisah. Migration dijalankan manual setelah memastikan target database. Dokumen disimpan pada volume persisten.

**Belum siap rilis:** kode `app/main.py` saat ini menolak startup staging/production sampai autentikasi sebenarnya disiapkan. Compose tidak melewati guard tersebut. Jangan memakai development/local identity untuk deployment publik.
