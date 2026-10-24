# Configuration

learning-tracker reads its settings only from `LT_*` environment variables. Copy `.env.example` to `.env`, fill in the four `<placeholders>`, and leave the rest at their defaults unless you need to change them.

Check what a service will run with (secrets are shown as `***`):

```bash
docker compose run --rm web lt check-config web
docker compose run --rm worker lt check-config worker
```

## Rules

- **Fail closed.** A service refuses to start, and lists every problem at once, when:
  - a required value is missing or still a `<placeholder>`;
  - a value is malformed or out of range;
  - an `LT_*` name is unknown (a typo such as `LT_INVITE_REQURED` gets a "did you mean" hint);
  - a service is given a setting it does not use. For secrets this is deliberate: the fetcher must never hold the database password or the AI key.
- **An empty value means "use the default".**
- **Booleans** are exactly `true` or `false`. Anything else (`yes`, `1`, `True`) is refused, so a typo cannot silently turn the invite gate off.
- **No error message contains a secret value.**

## Required

| Variable | Used by | Notes |
|---|---|---|
| `LT_SECRET_KEY` | web, worker | At least 32 random bytes, base64: `openssl rand -base64 48`. Low-randomness keys are refused. |
| `LT_DB_PASSWORD` | web, worker | Password for the bundled Postgres. Not needed if you set `LT_DATABASE_URL` for an external Postgres. |
| `LT_PUBLIC_ORIGIN` | web, worker | The address users open, e.g. `https://reads.example.com`. Must be `https://` unless the host is `localhost` or `127.0.0.1`. Used for the CSRF check and for links in emails. |
| `LT_ANTHROPIC_API_KEY` | worker | Your own key. Also set a monthly spend limit in the provider's console. |

## Rotating `LT_SECRET_KEY`

The app never uses `LT_SECRET_KEY` directly. It derives a separate key for sessions, invite codes, IP hashing, password-reset links and email-confirmation links. Changing `LT_SECRET_KEY` therefore:

- signs everyone out;
- voids every unredeemed invite code (make new ones);
- invalidates outstanding password-reset and email-confirmation links;
- resets rate-limit counters.

Saved data, topics and lists are not affected.

## All settings

| Variable | Used by | Default | Allowed |
|---|---|---|---|
| `LT_DATABASE_URL` | web, worker | built from `LT_DB_PASSWORD` | `postgresql+psycopg://…`, for an external Postgres only |
| `LT_INVITE_REQUIRED` | web | `true` | `true`/`false` |
| `LT_MAX_USERS` | web | `50` | 1–100000 |
| `LT_SESSION_DAYS` | web | `30` | 1–90 |
| `LT_MAX_TOPICS_PER_USER` | web | `3` | 1–10 |
| `LT_TRUSTED_PROXY_IPS` | web | empty | IP addresses of your reverse proxy; `*` and ranges are refused |
| `LT_BIND_ADDRESS` | compose | `127.0.0.1` | IP address the app port is published on |
| `LT_HTTP_PORT` | compose | `8080` | 1–65535 |
| `LT_PUBLIC_HOSTNAME` | compose (`tls` profile) | empty | hostname for the bundled TLS proxy |
| `LT_EDGE_SUBNET` / `LT_CADDY_IP` | compose | `172.31.251.0/24` / `172.31.251.10` | change only if the subnet clashes |
| `LT_FETCH_EGRESS_SUBNET` | compose | `172.31.250.0/24` | the subnet your host firewall rule fences off |
| `LT_BACKUP_KEEP_DAYS` | backup script | `14` | 1–365 |
| `LT_DEBUG` | all | `false` | `true` only with a localhost origin |
| `LT_LOG_LEVEL` | all | `info` | `debug`, `info`, `warning`, `error` |
| `LT_MODEL_SEARCH`, `LT_MODEL_PROFILE`, `LT_MODEL_SCREEN`, `LT_MODEL_CHOOSE`, `LT_MODEL_EXPLAIN` | worker | `claude-haiku-5-5` | a model id |
| `LT_PRICE_OVERRIDES` | worker | `{}` | JSON `{"model": {"in": 0.1, "out": 0.5}}`, USD per million tokens |
| `LT_SPEND_CAP_INSTANCE_MONTHLY_USD` | worker | `10` | 0–100000; `0` pauses all paid calls |
| `LT_SPEND_CAP_USER_MONTHLY_USD` | worker | `1.50` | 0–100000 |
| `LT_SPEND_CAP_USER_DAILY_USD` | worker | `0.15` | 0–100000 |
| `LT_SEARCHES_PER_LIST_MAX` | worker | `2` | 0–4 |
| `LT_SEARCH_RESULT_TOKEN_ALLOWANCE` | worker | `15000` | 5000–200000 input tokens reserved per search |
| `LT_SOURCE_ANTHROPIC_SEARCH` | worker | `true` | `true`/`false` |
| `LT_SOURCE_HN` | worker | `false` | Hacker News story search |
| `LT_DISCUSSION_HN` | worker | `false` | "Gist of the HN thread" lines |
| `LT_PIPELINE_START` / `LT_LIST_READY_BY` | worker | `02:00` / `06:00` | HH:MM in each user's time zone; start must be earlier |
| `LT_WORKER_CONCURRENCY` | worker | `2` | 1–8 |
| `LT_RUN_BUDGET_SECONDS` | worker | `480` | 60–900 |
| `LT_DOMAIN_BLOCKLIST_FILE` | worker | empty | absolute path inside the container, one domain per line |
| `LT_RETENTION_DAYS_LISTS` | worker | `90` | 30–365 |
| `LT_RETENTION_DAYS_RUNS` | worker | `90` | 7–365 |
| `LT_FETCH_CONTACT_URL` | fetcher | the project repository | `https://` URL shown in the fetcher's User-Agent |
| `LT_SMTP_HOST` | web, worker | empty | set it to send reset and confirmation emails; empty means admin reset links only. Web reads it only to know a mailer exists; the SMTP credentials stay in the worker. |
| `LT_SMTP_PORT` | worker | `587` | `587` (STARTTLS) or `465` (TLS) |
| `LT_SMTP_TLS` | worker | `required` | only `required`; there is no plain-text option |
| `LT_SMTP_USER` | worker | empty | |
| `LT_SMTP_PASSWORD` | worker | empty | required when `LT_SMTP_HOST` is set |
| `LT_SMTP_FROM` | worker | empty | one address, e.g. `Reads <no-reply@reads.example.com>`; required with `LT_SMTP_HOST` |
| `LT_RESET_TOKEN_MINUTES` | web, worker | `30` | 10–60 |
| `LT_CONFIRM_TOKEN_HOURS` | web, worker | `24` | 1–72 |
