# Supabase: sign-in and case records

OrderGuard AI can use Supabase for two things, each switched on by its key in `.env`:

| Feature | Key | What happens |
|---|---|---|
| **Sign-in** | `SUPABASE_PUBLISHABLE_KEY` | The whole dashboard needs a Supabase account. Approve, reject and undo are recorded against the signed-in person. |
| **Case records** | `SUPABASE_SECRET_KEY` | Every exception, investigation step, proposed fix, decision, action and customer message is copied to Supabase every 3 seconds. |

With neither key set, the project runs exactly as before: local, offline, no login.

The mock company systems (orders, stock, suppliers…) stay in the local demo database, so **Reset demo** and the scenarios keep working even without internet. Each demo session is a *run*: Reset demo starts a new one, so earlier runs are never overwritten.

## Set up (10 minutes)

### 1. Create the tables
Supabase → **SQL Editor** → **New query** → paste all of [`schema.sql`](schema.sql) → **Run**.
It creates the `oea_*` tables and the `oea_case_overview` view, and switches on Row Level Security. It is safe to run again.

### 2. Get the keys
Supabase → **Project Settings → API Keys**:
- **Publishable key** (`sb_publishable_…`): for sign-in. Safe to expose; the dashboard receives it from the backend.
- **Secret key** (`sb_secret_…`): for writing records. **Backend only.**

### 3. Put them in `.env`
```bash
cp .env.example .env        # Windows: copy .env.example .env
```
```ini
SUPABASE_URL=https://hfbizcbpuqhlegluaxxb.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
SUPABASE_SECRET_KEY=sb_secret_...
```
`.env` is in `.gitignore`, so the keys never reach GitHub. Check before every push: `git status` must not list `.env`.

### 4. Create the team's accounts
Supabase → **Authentication → Users → Add user → Create new user**: email + password, tick *Auto Confirm User*.
The dashboard and audit trail show the user's email. To show a name instead, run this in the SQL Editor once per person:
```sql
update auth.users set raw_user_meta_data = coalesce(raw_user_meta_data, '{}'::jsonb) || '{"full_name": "Asha Rao"}'
where email = 'asha@example.com';
```

Recommended: **Authentication → Sign In / Providers → turn off "Allow new users to sign up"**, so only people you add can get in.

### 5. Start and check
```bash
python scripts/dev.py
```
The terminal says `dashboard login required (Supabase Auth)` and `copying case records to Supabase`. Open http://localhost:8001, sign in, trigger a failure, then in Supabase → **Table Editor → oea_exceptions** the case appears within a few seconds.

## Security model

| Who | Can do |
|---|---|
| Browser (publishable key) | Sign in. Nothing else: the dashboard reads data only through the backend. |
| Signed-in user, directly against Supabase | **Read** the `oea_*` tables (Row Level Security policy "Signed-in users can read"). No writes. |
| Anonymous visitor | Nothing: no rows, no access to the view. |
| Backend (secret key) | Write the records. The secret key bypasses Row Level Security, so it stays on the server. |

- Every dashboard API call carries the user's access token; the backend checks it with Supabase (`GET /auth/v1/user`) and caches a valid answer for 60 seconds.
- `/health`, `/auth/config`, the dashboard files and n8n's `/n8n/*` calls stay open. The backend only listens on this computer (127.0.0.1).
- The supplier and system-log views read the mock systems on port 8000 directly; those are demo data, also only on this computer.

## Endpoints (`orchestrator/auth_api.py`)

| Endpoint | Needs sign-in | Returns |
|---|---|---|
| `GET /auth/config` | no | whether login is on, the project URL and publishable key |
| `GET /auth/me` | yes | the signed-in user |
| `GET /supabase/status` | yes | copy on/off, current run, last success, last error |
| `POST /supabase/sync` | yes | copy now instead of waiting |

The copying itself is in `orchestrator/supabase_integration.py`.

## Tables

| Table | One row per |
|---|---|
| `oea_runs` | demo session (new on start and after each Reset demo) |
| `oea_exceptions` | case |
| `oea_investigation_steps` | step of an investigation (checks, reasoning, policy, decisions) |
| `oea_proposed_actions` | ranked fix option |
| `oea_resolutions` | how a case ended (removed again if the fix is undone) |
| `oea_action_log` | action executed in the order systems, including undo |
| `oea_notifications` | message sent to a customer |
| `oea_case_overview` (view) | case with its outcome and money spent |

Example query in the SQL Editor:
```sql
select order_id, domain, severity, status, resolved_by, cost_spent
from oea_case_overview order by created_at desc limit 20;
```

## Good to know
- If Supabase is unreachable, the demo carries on; the copy catches up when it answers again. Errors show in the terminal and at `GET /supabase/status`.
- Scripts that call the API without a browser (`n8n/e2e_check.py`, `scripts/demo_scenario.py`) need login off: set `OEA_REQUIRE_LOGIN=0` (records are still copied).
- To start fresh in Supabase: `truncate oea_runs cascade;` in the SQL Editor deletes all copied records.
