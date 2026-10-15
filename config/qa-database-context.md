# QA Database Agent — Domain Knowledge Base

Persistent knowledge for `qa-database-agent`. Read **in full** at the start of
every cross-check (`## Database` + `## Known Patterns`). Manually curated — the
agent may *propose* schema notes (`## 🌱 Schema Notes` in its report), but only
the user's explicit yes gets one appended here; it never proposes causal/domain
claims. Update it by hand when a cross-check surfaces something durable.

This file ships as a **fill-in-the-blanks template** — the example rows below
show the expected shape, not a real schema.

---

## Database

Read-only cross-check via `config/db/.env`. CLI:

```bash
workflow db-info                                              # connectivity + version
workflow db-tables [--schema=public]                          # explore schema
workflow db-describe <table> [--schema=public]                # columns for a table
workflow db-query "<SELECT ...>" [--limit=200]                 # the actual check
```

**A DB row is CURRENT state, not history** — unless the table below is
explicitly marked as an audit/history table with its own per-row timestamp,
a query only tells you "what is true right now", not "what was true at the
incident time". Cite it accordingly (see `qa-database-agent` Rule 2).

**Check your timestamp storage convention before trusting a comparison.** Some
schemas store business timestamps as epoch integers (seconds since epoch)
rather than a native timestamp type — if yours does, cast before citing:
`to_timestamp(<col>) AT TIME ZONE 'UTC'`. Verify empirically once per table
family (cast one known row and compare with what the admin panel / logs show),
don't assume. Record the result here, per column.

**Check your money-column scaling convention before citing an amount.** Some
schemas store monetary amounts in minor units (e.g. cents, so ÷100 to get the
real-world value) rather than face value — citing the raw integer as-is can
overstate a value 100× and manufacture a false match against a user-reported
figure. Verify empirically (compare a raw value against what the admin
panel/user actually sees for the same row) before trusting the scale for a
table you haven't checked yet — don't assume every money-shaped column uses
the same convention.

**Fill in your own tables** — one row per table the agent is likely to need.
Mark audit/history tables explicitly: their per-row timestamp IS a historical
fact, everything else is a snapshot.

| Table | Key columns | What it's for |
|---|---|---|
| `users` *(example)* | `id`, `public_id` (external-facing uuid), `status` (`<ENUM_A>`/`<ENUM_B>`), `currency`, `<balance columns>` | Core user/account row. `id` is what other tables' `user_id` FK references |
| `orders` *(example)* | `id`, `user_id`, `amount` (minor units? face value? — verify), `status` (`PENDING`/`DONE`/`FAILED`/...), `created_at` (epoch? timestamptz? — verify) | One order/transaction attempt — usually the first table for "is it still stuck?" questions |
| `balance_history` *(example)* | `user_id`, `type` (enum reason), `delta`, `note`, `created_at` | **Audit table** — one row per balance mutation, so `created_at` here IS a historical fact, not "now" |
| `status_history` *(example)* | `user_id`, `status_id`, `timestamp` | **Audit table** — use instead of `users.status` when the question is "what was the status back then" |

Enum reference (`\dT+ <type>` or `select enumlabel from pg_enum ...` to
re-verify if these drift):
- `<order_status_type>`: PENDING, DONE, FAILED, ...
- `<user_status_type>`: ...

---

## Known Patterns

Confirmed business rules that change what a query result *means* — e.g. "a
NULL `<column>` is the expected outcome when <condition>, not a defect", or
"for a linked-account group, the decision is always keyed off the primary
account". None recorded yet — add entries here as cross-checks (or
`qa-logs` investigations) confirm durable findings. One entry per rule:
the rule, how it was confirmed, and which table/column it affects.
