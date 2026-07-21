---
name: qa-database
description: >
  Read-only Postgres cross-check via `qa-database-agent`, second-line to
  `/qa-logs`: takes an entity id + a specific question (or a Jira key) and
  checks the CURRENT database state to confirm or refute it. Not a
  replacement for log-based root-cause investigation — usually reached via
  a `/qa-logs` handoff when logs alone can't resolve something a DB
  snapshot could, but callable directly for a quick current-state check.
  Use when: "проверь в БД юзера X", "всё ещё висит в PENDING?", "сверь с
  базой", "qa-database DEV-XXXX", "какой сейчас статус у X".
---

# /qa-database

Entry point for a read-only Postgres cross-check. Coordinates
`qa-database-agent` — either called directly for a standalone current-state
question, or as the accepted follow-up from a `/qa-logs` escalation offer.

## Usage

```
/qa-database "юзер 42, всё ещё в статусе PENDING по выводу?"     # free text
/qa-database DEV-3102                                             # Jira key — agent reads the ticket
```

Direct invocation skips the `/qa-logs` handoff shape — just give an entity id
and the concrete claim to check.

## Algorithm

### 1. Parse input

- A Jira key anywhere in the args → Jira-key mode.
- Otherwise → free-text mode: entity id + the specific question.
- Neither a Jira key, an entity id, nor a checkable question → ask: "Какую
  сущность и какой конкретно вопрос проверить в БД? (например: 'юзер 42,
  всё ещё PENDING по выводу?')"

### 2. Check DB is configured

```bash
workflow db-info
```

Failure (missing credentials / unreachable / tunnel down) → tell the user,
point at `config/db/.env` (`DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`, or
`DB_SSH_*` for a bastion tunnel), do not launch the agent.

### 3. Launch qa-database-agent

```python
Agent(subagent_type="qa-database-agent", prompt="""
{Jira key mode: "Jira key: {KEY}. Read the task (rtk summary workflow jira-task {KEY})
and its comments (workflow jira-comments {KEY}) to extract the entity id and
the specific question to check."}
{Free-text mode: "Entity + question from the user: {free text}."}
{Handoff mode (from /qa-logs): "Handoff from a qa-logs-agent investigation.
Entity: {entity}. Question to confirm/refute (current DB state): {question}."}

Read config/qa-logs-context.md → ## Database first.

Then follow your normal Step 0-4 workflow: scope the question, locate the
schema, query, interpret, verdict. Produce the full report in the standard
output format.
""")
```

### 4. After agent output — relay it

The report is short by design (a snapshot check, not a causal chain) — relay
it directly. If the verdict is `inconclusive` and the agent's recommendation
says the real answer needs log-based reconstruction, mention that `/qa-logs`
is the right next step (don't auto-launch it — a new symptom description
would need re-scoping anyway).

### 5. After the report — Seed Mode (schema notes only)

If the report has a `## 🌱 Schema Notes` section, show it to the user as-is
and ask:

> "Агент выучил кое-что новое про схему БД (не про баг — просто где что
> лежит). Добавить в `config/qa-logs-context.md` → `## Database`? (да / нет)"

On yes, append each note as a new row/line in that table (or a short bullet
under it if it doesn't fit the table shape) — schema/navigation facts only,
never rephrase into a causal claim. On no, drop it — don't ask again this
session. No `## 🌱 Schema Notes` section → skip this step entirely.

### 6. After the report — offer to file a bug

> "Завести баг в Jira по этому расследованию? (да / нет)"

If yes, hand the finding to whatever ticket-creation flow your orchestrator
has — this agent never files tickets itself, same as `/qa-logs` step 6.

## Rules

- Never launch the agent without an entity id + a specific, checkable question
- Never auto-file a Jira bug — always ask first
- DB is read-only — this skill never offers or implies a write/fix action
- Seed Mode is schema-notes only, and opt-in per note: the agent may propose a
  table/column/join fact it had to discover the hard way; only the user's
  explicit yes gets it appended to `config/qa-logs-context.md` → `## Database`.
  It never proposes causal/domain claims — those stay a manual/`qa-logs-agent`
  judgment call
- One `/qa-database` call = one cross-check (not a general DB browsing session)

## Output Contract

The agent produces the complete report. Relay it directly to the user.

Required: `## 🔎 Проверка`, `## Вывод`.
Optional: `## 🔧 Рекомендации`, `## 🌱 Schema Notes`.

## Fail-fast

```
BLOCKED_NO_INPUT
Neither a Jira key nor a usable entity id + question was given
Needed from user: an entity id + the concrete claim to confirm/refute, or a Jira key
```
