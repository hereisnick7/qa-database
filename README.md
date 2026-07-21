# /qa-database — DB Cross-Check Agent v1.0

> Скилл + суб-агент для Claude Code. Read-only снимок текущего состояния Postgres — второй эшелон после `/qa-logs`: когда логов недостаточно, чтобы закрыть конкретный вопрос, а снимок БД прямо сейчас может.

---

## Что делает

Логи отвечают на "что произошло и почему". Этот агент отвечает на другой вопрос — "что верно прямо сейчас": застрял ли энтити в статусе, существуют ли ещё дубли строк, что реально лежит в поле, которое логи не смогли объяснить.

1. Принимает вопрос тремя способами: явный хэндофф от `/qa-logs` (эскалация из отчёта), свободный текст (энтити + конкретная проверяемая claim), или ключ Jira
2. Смотрит схему БД (`config/qa-logs-context.md` → `## Database`, либо `db-tables`/`db-describe` вживую, если таблица не задокументирована)
3. Делает один точечный `SELECT` — не exploration, а проверка конкретного факта
4. Даёт вердикт: **Confirms** / **Refutes** / **Inconclusive** — привязанный к исходному вопросу
5. Никогда не путает "текущее состояние" с "историческим фактом" — кроме audit/history таблиц (где у строки есть собственный timestamp), любое значение помечается как факт на момент запроса, не на момент инцидента
6. Опционально предлагает Schema Notes — что узнал про схему по ходу (не про баг, просто где что лежит), только с подтверждением пользователя
7. Только отчёт — тикет в Jira заводится отдельным явным шагом

**Это не general-purpose DB browser.** Каждое расследование начинается с конкретного вопроса — агент не "смотрит, что там у юзера", он подтверждает или опровергает конкретную claim.

---

## Использование

```
/qa-database "юзер 42, всё ещё в статусе PENDING по выводу?"     # свободный текст
/qa-database DEV-3102                                             # Jira-ключ — агент сам читает задачу
```

Обычно вызывается не напрямую, а как предложенный follow-up после `/qa-logs`, когда отчёт агента логов помечает `escalate_db: yes` в футере — но можно звать и напрямую для быстрой сверки.

---

## Формат отчёта

```
## 🔎 Проверка     ← 1-2 [FACT] (DB, as of ...) записи — запрос, результат, что это значит
## Вывод           ← Confirms | Refutes | Inconclusive, привязано к вопросу
## 🔧 Рекомендации  ← опционально, гипотеза куда смотреть дальше (не фикс)
## 🌱 Schema Notes  ← опционально, что узнал про схему (не про баг)
```

---

## Структура файлов

```
.claude/
  agents/
    qa-database-agent.md      ← суб-агент (основная логика)
  skills/
    qa-database/
      SKILL.md                ← скилл /qa-database (оркестратор)
config/
  qa-logs-context.md          ← та же база знаний, что и у qa-logs — секция ## Database (заполни под свою схему)
modules/
  db/
    client.py                 ← read-only Postgres клиент (client-side + session-level + timeout guard)
    cli.py                    ← workflow db-info / db-tables / db-describe / db-query
  jira/                       ← нужен только для режима "по Jira-ключу"
```

Если уже используешь `/qa-logs` из отдельного репо — просто скопируй `.claude/agents/qa-database-agent.md` и `.claude/skills/qa-database/` в тот же проект вместо клонирования этого репо отдельно; оба агента читают один и тот же `config/qa-logs-context.md`.

---

## Установка

### Требования

- [Claude Code](https://claude.ai/code) CLI
- Python 3.9+
- Postgres с read-only доступом (отдельный DB-юзер с `SELECT`-грантами настоятельно рекомендуется, хотя клиент и сам форсит read-only)

### Установка workflow CLI

```bash
git clone https://github.com/hereisnick7/qa-database.git
cd qa-database
pip install -e .
```

**Windows:** используй `workflow.cmd` вместо `workflow`.

### Настройка credentials

```bash
mkdir -p config/db config/jira
cp config/example/db/.env.example config/db/.env
cp config/example/jira/.env.example config/jira/.env   # опционально, только для Jira-режима
```

Открой `config/db/.env`:
```
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=your-db-name
DB_USER=readonly-user
DB_PASSWORD=сюда-вставить-пароль
```

**Read-only гарантируется тремя независимыми способами:** клиент принимает только `SELECT`/`WITH`/`EXPLAIN`/`SHOW`/`TABLE` (`assert_read_only`), сама Postgres-сессия открывается с `default_transaction_read_only = on`, плюс `statement_timeout`. БД-юзер с `SELECT`-only грантами — дополнительный, не единственный уровень защиты.

**Если БД не доступна напрямую с этой машины** — SSH-туннель через bastion (см. закомментированные `DB_SSH_*` переменные в `.env.example`).

### Настройка базы знаний

Открой `config/qa-logs-context.md` → секцию `## Database` и заполни под свою схему: таблицы, ключевые колонки, конвенция хранения timestamp (epoch vs native), конвенция хранения денег (minor units vs face value) — читай инструкции внутри секции, там объяснено что и зачем проверять на своих данных.

### Запуск

```bash
cd qa-database
claude
```

```
/qa-database "юзер 42, всё ещё висит вывод в PENDING?"
```

**Windows:** если команда `workflow` не найдена — используй `workflow.cmd` напрямую.

---

## История версий

| Версия | Что добавлено |
|---|---|
| v1.0 | Первый релиз: read-only Postgres cross-check как второй эшелон эскалации от `/qa-logs`; три независимых read-only гарантии; различение current-state vs audit/history таблиц; опциональные Schema Notes с подтверждением пользователя |

---

## Лицензия

MIT
