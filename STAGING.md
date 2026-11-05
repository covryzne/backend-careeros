# Backend staging: Supabase Auth and deployment

Backend staging requires authenticated Supabase user access tokens. Development/test can still use local identity when explicitly enabled. API requests never accept an owner ID from the browser; the verified JWT `sub` becomes the application `users.id`. Existing owner-scoped services and lazy user creation remain in use; no schema change is introduced by authentication.

## Server configuration

Work in `/home/azureuser/careeros-job-tracker/backend-careeros`. Keep `.env.staging` private on the server. After bringing this branch's changes to the server, configure:

```dotenv
APP_ENV=staging
ALLOW_LOCAL_IDENTITY=false
DATABASE_URL=postgresql+psycopg://REPLACE_USER:REPLACE_URL_ENCODED_PASSWORD@REPLACE_HOST:REPLACE_PORT/postgres?sslmode=require
SUPABASE_URL=https://REPLACE_STAGING_PROJECT_REF.supabase.co
SUPABASE_JWT_AUDIENCE=authenticated
ALLOWED_ORIGINS=["https://staging.covryzne.me"]
PROVIDER_ENCRYPTION_KEY=REPLACE_STABLE_STAGING_FERNET_KEY
DOCUMENT_STORAGE_DIR=/data/careeros/documents
AGENT_RUNTIME_ALLOWED=false
AGENT_WORKER_EMBEDDED=false
AGENT_ENABLED=false
AGENT_INTERNAL_WRITES_ENABLED=false
AGENT_APPROVALS_ENABLED=false
```

An existing `SUPABASE_JWKS_URL` must equal `SUPABASE_URL` plus `/auth/v1/.well-known/jwks.json`; it can be omitted to derive it automatically. `AUTH_JWKS_TIMEOUT_SECONDS` defaults to 5 and `AUTH_JWKS_CACHE_SECONDS` to 300. Do not quote whole values when using the Compose raw env format. URL-encode reserved characters in database credentials.

Use the staging project's database and Auth issuer together. Configure Supabase Auth with an asymmetric signing key (ES256 or RS256); legacy HS256 is deliberately not accepted. Never use the database password, publishable key or secret API key as a user Bearer token. API keys are not needed by this verifier. JWKS is fetched from a fixed configured URL, with timeout and TTL caching; no URL in the untrusted JWT controls key discovery. Key rotation can require a JWKS refresh; current client caching/cooldown applies. Local verification does not check immediate session revocation; existing valid access tokens remain valid until expiry.

Startup validates environment, local-identity setting, Auth configuration, explicit CORS origins and Fernet key. Misconfiguration aborts startup. It does not call Supabase or migrate the database during startup.

## Build, migrate, serve

```bash
chmod 600 .env.staging
docker compose --env-file .env.staging -f compose.staging.yml config --quiet
docker compose --env-file .env.staging -f compose.staging.yml build backend
docker compose --env-file .env.staging -f compose.staging.yml run --rm --no-deps backend python -c "from app.core.config import settings; settings.validate_deployment(); print('Staging configuration valid')"
docker compose --env-file .env.staging -f compose.staging.yml run --rm --no-deps backend python -c "from app.db.session import database_health; result=database_health(); print('database='+result); raise SystemExit(0 if result=='ok' else 1)"
docker compose --env-file .env.staging -f compose.staging.yml run --rm --no-deps backend python -m alembic upgrade head
docker compose --env-file .env.staging -f compose.staging.yml run --rm --no-deps backend python -m alembic current
docker compose --env-file .env.staging -f compose.staging.yml up -d backend
docker compose --env-file .env.staging -f compose.staging.yml ps
docker compose --env-file .env.staging -f compose.staging.yml logs --tail=100 backend
curl -i http://127.0.0.1:8002/health
curl -i http://127.0.0.1:8002/api/v1/profile
```

Run commands sequentially and resolve failures before continuing. Check the migration target before applying migrations. Expected: health has `database=ok`; protected profile returns **401 without a token**. A correctly authenticated new user without a seeded/created profile gets 404, not 401. A syntactically valid configuration and healthy DB do not prove real-project JWT interoperability; test a real staging login/access token before release.

The frontend must send `Authorization: Bearer <user-access-token>` from a Supabase login; that frontend work is not part of this backend change. Existing local frontend requests without a token will correctly receive 401 in staging.

The existing Nginx routing `/api/v1/* -> 127.0.0.1:8002` works unchanged. `/health` is outside that prefix: use the local health URL above. Do not interpret a public `/health` response from frontend routing as the backend's readiness check.

## Optional staging seed

Create a staging Supabase Auth user first (dashboard or the chosen login flow) and copy its UUID from that staging project. Use the same owner for both seeders:

```bash
docker compose --env-file .env.staging -f compose.staging.yml run --rm --no-deps backend python -m scripts.seed_real_profile --owner-id REPLACE_STAGING_AUTH_USER_UUID --confirm-staging
docker compose --env-file .env.staging -f compose.staging.yml run --rm --no-deps backend python -m scripts.seed_jobs --owner-id REPLACE_STAGING_AUTH_USER_UUID --confirm-staging
```

Scripts are now included in the image, so no Windows/host bind mount is needed. The seeder verifies that this UUID exists in `auth.users` on the target database, then uses shared application services to create the application user and owner-scoped records. The migration/seed DB role must be able to read `auth.users` for this check; do not grant the browser direct access. Production seeding is rejected. Staging requires both flags and rejects the local owner. Default development commands still use the original local owner. Existing fill/merge behavior and `--overwrite` semantics are unchanged; review the real-profile seed data before storing it in staging.

## Verification and boundaries

- Signed synthetic ES256 and RS256 tests cover ownership isolation, tampering, expired tokens, wrong issuer/audience/role/subject and unavailable key discovery.
- Startup tests cover missing Auth configuration, local identity leakage, wildcard origins and missing encryption keys.
- Real JWKS HTTP behavior is exercised by `tests/test_auth_simulation.py` using the local Postman fixture. See `postman/simulations/staging-auth.context.yaml` for evidence and replay instructions.
- No live Supabase credentials, database or VM are accessed during these tests. Docker image build and server deployment must still be verified in the target environment.
- Crawler worker's staging guard and headed/Xvfb support are separate work; this backend API change does not enable them. Keep agent runtime disabled during the initial staging rollout.

References: [Supabase JWTs](https://supabase.com/docs/guides/auth/jwts), [PyJWT JWKS verification](https://pyjwt.readthedocs.io/en/latest/usage.html).
