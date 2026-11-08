# Login session contract

FastAPI route declarations and `/openapi.json` are the schema source of truth.

Each account has at most one active login session across browsers. All authenticated
resource requests require an active server-side login lease.

When the same account signs in from another browser, show: "Akun ini sedang aktif
di browser lain. Ingin pindah ke browser ini?" Offer "Batal" and "Pindah ke browser
ini". Cancellation preserves the previous browser session; confirmation revokes it.
The previous browser receives: "Sesi Anda berakhir karena akun dipindahkan ke
browser lain."
Leases last 21,600 seconds from successful login/activation, with no sliding
extension. Token refresh, repeated activation of the same Supabase `session_id`,
and opening tabs preserve the original deadline. Revoked leases cannot reactivate.

## Local credentials

- `POST /api/v1/local-auth/login`: `{ email, password, takeover?: boolean }`.
  Credentials are checked before reporting another active session. A successful
  response contains `{ token, expires_in, expires_at, user }`. The frontend keeps
  the token in an HttpOnly cookie and never returns it in a browser JSON response.
- Without confirmation, another active lease returns HTTP 409 with
  `error.code: SESSION_ACTIVE_ELSEWHERE`. Cancelling sends no takeover request.
- Resend valid credentials with `takeover: true` after confirmation to revoke the
  previous lease and create a new six-hour lease in one transaction.
- A valid bearer session supplied to local login with the same account email
  resumes that exact token and its original deadline. A different account or
  registration returns 409 `BROWSER_SESSION_ACTIVE`: logout before switching.
- `GET /api/v1/local-auth/session` returns display identity plus lease expiry.
- `POST /api/v1/local-auth/logout` returns 204 and revokes only the supplied lease.

## Supabase password, Google and email confirmation

The frontend authenticates with the provider, then explicitly activates the login
lease. Provider tokens remain server-side. Only signed, verified user JWTs with a
valid UUID `session_id` can call these operations:

- `POST /api/v1/auth-sessions`, body `{ takeover?: boolean }`: activate the lease;
  return 200 `{ expires_in, expires_at }`. Another active session returns 409
  `SESSION_ACTIVE_ELSEWHERE` unless takeover is explicitly true.
- `GET /api/v1/auth-sessions/current`: check the lease and return remaining expiry.
- `DELETE /api/v1/auth-sessions/current`: return 204 and revoke only this session.

Opening `/login` with a verified active session redirects to the dashboard.
Frontend `/auth/login` resumes the same account's existing session before
contacting the provider, preserving the original six-hour deadline. A different
account and `/auth/register` reject a currently active browser session.
Google initiation and
callback/confirmation also guard against replacing an active browser account.
Google/email callbacks with a session conflict redirect to
`/login?auth_error=session_active`. Explicit confirmation calls same-origin
`POST /auth/takeover`; cancellation signs out only the pending provider session.

## Errors and persistence

Errors use `{ error: { code, message, ... } }` and private/no-store responses.
Protected requests return 401 `SESSION_REPLACED` after takeover,
`SESSION_EXPIRED` after the deadline/logout, or `SESSION_INVALID` for an unknown
lease. Invalid credentials remain 401; lockout remains 429. Availability errors
remain 503 and must not silently log out a healthy session.

Apply Alembic migration `0021_single_login_session` before starting the updated
services. Existing local sessions are copied with their original eight-hour
expiry reduced by two hours; older concurrent sessions are marked replaced.
Existing Supabase sessions need a fresh login to establish a login lease.
The database locks each user during activation and has a partial unique index
allowing only one unrevoked session per account across API processes.
