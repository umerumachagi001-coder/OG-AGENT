-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- OrderGuard AI · Supabase schema
-- Run once in Supabase → SQL Editor → New query → paste → Run. Safe to run again (idempotent).
--
-- What lives here: the case records the automation produces (exceptions, every investigation step,
-- proposed fixes, decisions, the action log, customer notifications). The mock company systems
-- (orders, stock, suppliers…) stay in the local demo database so "Reset demo" keeps working.
--
-- Every demo session is a "run". Reset demo starts a new run, so case ids can restart from 13 without
-- overwriting earlier runs: rows are keyed by (run_id, id).
--
-- Security: Row Level Security is ON for every table. Signed-in users (role `authenticated`) can READ.
-- Nobody can write through the public API; only the backend writes, with the secret key, which
-- bypasses RLS. Anonymous visitors (role `anon`) can see nothing.
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

-- ───────── demo sessions ─────────
create table if not exists public.oea_runs (
    id          uuid        primary key,
    started_at  timestamptz not null default now(),
    engine      text,                                  -- n8n | python
    host        text                                   -- machine that ran the demo
);

-- ───────── exceptions (one per broken order + problem type) ─────────
create table if not exists public.oea_exceptions (
    run_id       uuid        not null references public.oea_runs(id) on delete cascade,
    id           integer     not null,
    order_id     text        not null,
    domain       text        not null check (domain in ('inventory','supplier','payment','pricing','deadline')),
    severity     text        not null check (severity in ('low','medium','high','critical')),
    status       text        not null,                 -- open | investigating | awaiting_approval | auto_resolved | resolved | rejected | failed
    symptom      text,                                 -- what the detector saw
    root_cause   text,                                 -- the investigation's assessment
    confidence   real,
    created_at   timestamptz not null,
    resolved_at  timestamptz,
    origin       text        not null default 'live',  -- live | history (seeded past cases)
    synced_at    timestamptz not null default now(),
    primary key (run_id, id)
);

-- ───────── investigation trail: every check, thought, decision ─────────
create table if not exists public.oea_investigation_steps (
    run_id        uuid        not null references public.oea_runs(id) on delete cascade,
    id            integer     not null,
    exception_id  integer     not null,
    step_no       integer     not null,
    type          text        not null check (type in ('thought','tool_call','tool_result','decision')),
    content       jsonb       not null,
    ts            timestamptz not null,
    primary key (run_id, id),
    foreign key (run_id, exception_id) references public.oea_exceptions(run_id, id) on delete cascade
);

-- ───────── proposed fixes (ranked options) ─────────
create table if not exists public.oea_proposed_actions (
    run_id             uuid    not null references public.oea_runs(id) on delete cascade,
    id                 integer not null,
    exception_id       integer not null,
    rank               integer not null,
    action_type        text    not null,               -- e.g. reallocate_stock+expedite_shipping
    params             jsonb   not null,               -- title, steps, impact, rationale, policy verdict
    cost               numeric(12,2) not null default 0,
    risk               text    not null default 'low' check (risk in ('low','medium','high')),
    rationale          text,
    requires_approval  boolean not null default false,
    status             text    not null default 'proposed',  -- proposed | approved | rejected | executed | failed
    synced_at          timestamptz not null default now(),
    primary key (run_id, id),
    foreign key (run_id, exception_id) references public.oea_exceptions(run_id, id) on delete cascade
);

-- ───────── how each case ended ─────────
create table if not exists public.oea_resolutions (
    run_id              uuid    not null references public.oea_runs(id) on delete cascade,
    exception_id        integer not null,
    action_id           integer,
    outcome             text    not null,              -- success | failed | rejected
    human_feedback      text,
    resolved_by         text    not null,              -- agent | the signed-in person
    minutes_to_resolve  real,
    synced_at           timestamptz not null default now(),
    primary key (run_id, exception_id),
    foreign key (run_id, exception_id) references public.oea_exceptions(run_id, id) on delete cascade
);

-- ───────── every action the systems executed (incl. undo) ─────────
create table if not exists public.oea_action_log (
    run_id       uuid    not null references public.oea_runs(id) on delete cascade,
    id           integer not null,
    action_type  text    not null,
    params       jsonb   not null,
    result       jsonb   not null,
    ok           boolean not null,
    cost         numeric(12,2) not null default 0,
    ts           timestamptz not null,
    primary key (run_id, id)
);

-- ───────── messages sent to customers ─────────
create table if not exists public.oea_notifications (
    run_id    uuid    not null references public.oea_runs(id) on delete cascade,
    id        integer not null,
    order_id  text    not null,
    channel   text    not null default 'email',
    message   text    not null,
    ts        timestamptz not null,
    primary key (run_id, id)
);

-- ───────── indexes for the usual questions ─────────
create index if not exists oea_exceptions_status_idx  on public.oea_exceptions (status);
create index if not exists oea_exceptions_order_idx   on public.oea_exceptions (order_id);
create index if not exists oea_exceptions_created_idx on public.oea_exceptions (created_at desc);
create index if not exists oea_steps_case_idx         on public.oea_investigation_steps (run_id, exception_id, step_no);
create index if not exists oea_actions_case_idx       on public.oea_proposed_actions (run_id, exception_id);
create index if not exists oea_action_log_ts_idx      on public.oea_action_log (ts desc);

-- ───────── Row Level Security: signed-in users read, nobody writes through the API ─────────
do $$
declare t text;
begin
    foreach t in array array['oea_runs','oea_exceptions','oea_investigation_steps','oea_proposed_actions',
                             'oea_resolutions','oea_action_log','oea_notifications']
    loop
        execute format('alter table public.%I enable row level security', t);
        execute format('drop policy if exists "Signed-in users can read" on public.%I', t);
        execute format('create policy "Signed-in users can read" on public.%I for select to authenticated using (true)', t);
        execute format('revoke insert, update, delete, truncate on public.%I from anon, authenticated', t);
    end loop;
end $$;

-- ───────── handy view: one row per case with its outcome (respects RLS of the tables) ─────────
create or replace view public.oea_case_overview
with (security_invoker = true) as
select e.run_id,
       e.id               as case_id,
       e.order_id,
       e.domain,
       e.severity,
       e.status,
       e.root_cause,
       e.confidence,
       e.created_at,
       e.resolved_at,
       r.outcome,
       r.resolved_by,
       r.minutes_to_resolve,
       (select coalesce(sum(a.cost), 0) from public.oea_proposed_actions a
         where a.run_id = e.run_id and a.exception_id = e.id and a.status = 'executed') as cost_spent
from public.oea_exceptions e
left join public.oea_resolutions r on r.run_id = e.run_id and r.exception_id = e.id
where e.origin = 'live';

grant select on public.oea_case_overview to authenticated;
revoke all on public.oea_case_overview from anon;
