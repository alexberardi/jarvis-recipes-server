# Handoff — `feat/rs256-verify-and-settings-db`

**Date:** 2026-09-02
**Branch:** `feat/rs256-verify-and-settings-db` (pushed, no PR opened)
**Base:** `main`, which was exactly level with `origin/main` — no divergence to untangle.
**Commit:** `c3650f7` — 30 files, +621 / −268

Written for whoever picks this up next. It was a working tree full of
uncommitted changes; the commit is a rescue of that work, not a finished,
reviewed unit.

---

## ⚠️ Status: UNVERIFIED

**The test suite has never been run against this branch.** The commit *adds*
tests (`tests/test_health.py`, `tests/test_settings.py`, plus additions to
`tests/test_recipes.py`) and I have no evidence any of them pass. Treat a green
run as the first real milestone, not a formality.

```bash
poetry install
poetry run pytest
```

Also unapplied: the two new Alembic migrations have not been run against any
database.

---

## Goal of the branch

Two related pieces of work, plus the config cleanup the second one exposed.

### 1. RS256/HS256 dual-accept JWT verification (`jarvis_recipes/app/api/deps.py`)

This service needs to keep verifying tokens while `jarvis-auth` migrates from
HS256 to RS256 (see `prds/auth-rs256-migration.md` in the umbrella repo).

- Both algorithms are accepted during the migration window. The key is chosen
  by algorithm **family** (`SYMMETRIC_ALGORITHMS` / `ASYMMETRIC_ALGORITHMS`),
  never from one shared variable.

  **This is the security property, do not "simplify" it.** The RSA public key
  is published and readable. If one variable held "the key", an attacker could
  sign a token HS256 using that public key as the HMAC secret and be verified.
  Binding key material to the family means such a token is checked against
  `auth_secret_key` and fails.

- Anything outside the allowlist is rejected, including `"none"`.
- The RS256 public key is fetched from `jarvis-auth` `/auth/public-key` and
  cached for the process lifetime, so a **running** service keeps verifying if
  jarvis-auth goes down. Only a cold start during an outage fails, and it
  **fails closed** — an RS256 token we cannot check is not accepted.
- Implemented with plain `httpx`, deliberately **not** via `jarvis-auth-client`.
  This service is being decoupled from the Jarvis stack; taking a new Jarvis
  library dependency just to verify a token moves it the wrong way.
- `auth_algorithm` is no longer consulted for verification — it only ever
  governed what jarvis-auth *mints*. Dropped by migration `d4e5f6a7b8c9`.

**Nothing mints RS256 yet**, stack-wide. So on today's tokens this path is
exercised only on its HS256 branch. The RS256 branch needs a hand-minted token
to test — don't assume it works because the suite is green.

Requires `jarvis-auth`'s URL to be resolvable via `service_config.get_auth_url()`
(config-service discovery, or the `JARVIS_AUTH_BASE_URL` fallback). Harmless
today; **required before the RS256 flip**, or this service 401s everything the
moment jarvis-auth switches.

### 2. Runtime knobs → settings DB (`core/config.py`, `services/settings_service.py`)

Model names, queue retries, the parse-job abandon window, the image size cap
and the scraper User-Agent were runtime knobs living in `.env`. They now live in
the settings DB, keeping the same env vars as `env_fallback`. `config.py` retains
only secrets, service discovery, and bootstrap values — the house rule.

Two new migrations, linear off `b2c3d4e5f6g7`, single head:

| Revision | Down revision | Purpose |
|---|---|---|
| `c3d4e5f6a7b8` | `b2c3d4e5f6g7` | Prune phantom seeded settings, seed the real knobs |
| `d4e5f6a7b8c9` | `c3d4e5f6a7b8` | Drop `auth.algorithm` |

### 3. Incidental

Structured logging via the new `core/logging_config.py` across the workers
(`run_cleanup`, `run_parse_worker`, `run_rq_worker`); `env.template` documents
the discovery / app-credential and Redis blocks; `CLAUDE.md` added.

---

## Suggested next steps

1. `poetry run pytest` — get it green. Nothing below matters until it is.
2. Apply the migrations to a dev DB and confirm the settings rows land as
   expected and `auth.algorithm` is gone.
3. Exercise the HS256 path end-to-end against a real jarvis-auth token.
4. Mint an RS256 token by hand and confirm both the accept path and the
   fail-closed path (public key unreachable → 401, not a 500 and not an accept).
5. Then open a PR.

## Related

`jarvis-recipes-mobile` has a companion branch, `feat/keychain-tokens-and-tests`,
covering the client half (tokens to the OS keychain, single-flight refresh). The
two are independent — neither blocks the other.
