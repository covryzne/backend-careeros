# Local Auth dependency simulation

This fixture was built from `contracts/supabase-jwks.openapi.yml`, based on the public Supabase JWKS contract. It is not an owner-published mock. Owner-workspace discovery was skipped: the installed CLI 1.76.0 does not support the skill's `postman status` command. Nothing is pushed or deployed.

From the backend repository, terminal 1:

```bash
postman simulation run postman/simulations/staging-auth.sim.yaml --port 4519 --no-history --output ndjson
```

Terminal 2, with backend dependencies installed (PowerShell):

```powershell
$env:APP_ENV = 'development'
$env:DATABASE_URL = 'sqlite://'
$env:AUTH_SIMULATION_URL = 'http://127.0.0.1:4519'
python -m pytest tests/test_auth_simulation.py -q -p no:cacheprovider
```

On Linux, the equivalent is:

```bash
APP_ENV=development DATABASE_URL=sqlite:// AUTH_SIMULATION_URL=http://127.0.0.1:4519 python -m pytest tests/test_auth_simulation.py -q -p no:cacheprovider
```

The test runs the actual FastAPI/JWT path against SQLite and real HTTP JWKS discovery. Only in the test, the trusted endpoints are wired to the local stand-in. Deployment settings still require an HTTPS project origin. Test-generated asymmetric private keys never leave the Python process; only public keys reach the fixture's `/__test/control` endpoint. Stop terminal 1 with Ctrl+C after the test. This mock/control route is local test infrastructure, not part of the deployed backend and not a production authentication provider.

Inspect simulation request events: JWKS should show 200, 503, 200 for success, cold-cache outage and recovery. No tokens are sent to the mock; no log artifacts are committed. See `simulations/staging-auth.context.yaml` for evidence and unresolved live-provider checks.
