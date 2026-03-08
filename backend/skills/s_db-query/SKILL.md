---
name: Database Query
description: >
  Connect to and query databases: SQLite, PostgreSQL, MySQL. Run SQL, explore
  schemas, export results, and manage migrations.
  TRIGGER: "query database", "SQL", "sqlite", "postgres", "mysql", "database",
  "run query", "show tables", "schema", "db", "create table", "migration".
  DO NOT USE: for spreadsheet files (use xlsx), in-memory data manipulation
  (just use Python/Node), or API data (use api-test).
---

# Database Query — SQL Database Operations

Query, explore, and manage SQL databases directly. Supports SQLite (built-in),
PostgreSQL (via psql), and MySQL (via mysql CLI).

## Quick Start

```
"Query the users table in my SQLite database"
"Show me the schema of production.db"
"Run this SQL against my Postgres database"
"Export query results to CSV"
```

## Available Backends

| Backend | CLI | Status | Install |
|---------|-----|--------|---------|
| **SQLite** | `sqlite3` | Built-in (macOS) | — |
| **PostgreSQL** | `psql` | Install needed | `brew install libpq` |
| **MySQL** | `mysql` | Install needed | `brew install mysql-client` |

Check availability:
```bash
which sqlite3 psql mysql 2>/dev/null
```

If psql/mysql not installed and user needs them, install via brew (check
`auto_approve_installs` first).

---

## SQLite Operations

### Connect and Query

```bash
# One-shot query
sqlite3 /path/to/db.sqlite "SELECT * FROM users LIMIT 10;"

# With headers and column mode (readable output)
sqlite3 -header -column /path/to/db.sqlite "SELECT * FROM users LIMIT 10;"

# CSV output
sqlite3 -header -csv /path/to/db.sqlite "SELECT * FROM users;" > output.csv

# JSON output
sqlite3 -json /path/to/db.sqlite "SELECT * FROM users LIMIT 10;"
```

### Explore Schema

```bash
# List all tables
sqlite3 /path/to/db.sqlite ".tables"

# Show table schema (CREATE statement)
sqlite3 /path/to/db.sqlite ".schema users"

# Show all schemas
sqlite3 /path/to/db.sqlite ".schema"

# Table info (columns, types)
sqlite3 /path/to/db.sqlite "PRAGMA table_info(users);"

# Foreign keys
sqlite3 /path/to/db.sqlite "PRAGMA foreign_key_list(orders);"

# Indexes
sqlite3 /path/to/db.sqlite "PRAGMA index_list(users);"

# Row count
sqlite3 /path/to/db.sqlite "SELECT COUNT(*) FROM users;"
```

### Create and Modify

```bash
# Create table
sqlite3 /path/to/db.sqlite "CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT DEFAULT (datetime('now')),
  level TEXT NOT NULL,
  message TEXT
);"

# Insert data
sqlite3 /path/to/db.sqlite "INSERT INTO logs (level, message) VALUES ('INFO', 'test entry');"

# Alter table
sqlite3 /path/to/db.sqlite "ALTER TABLE users ADD COLUMN email TEXT;"
```

### Import/Export

```bash
# Import CSV
sqlite3 /path/to/db.sqlite ".mode csv" ".import /path/to/data.csv tablename"

# Export entire database as SQL dump
sqlite3 /path/to/db.sqlite ".dump" > backup.sql

# Export specific table
sqlite3 /path/to/db.sqlite ".dump users" > users.sql

# Restore from dump
sqlite3 /path/to/new.sqlite < backup.sql
```

---

## PostgreSQL Operations

### Connect and Query

```bash
# Connect with connection string
psql "postgresql://user:pass@host:5432/dbname" -c "SELECT * FROM users LIMIT 10;"

# With environment variables
PGHOST=localhost PGUSER=myuser PGDATABASE=mydb psql -c "SELECT * FROM users;"

# CSV output
psql "postgresql://..." -c "COPY (SELECT * FROM users) TO STDOUT WITH CSV HEADER;"

# JSON output
psql "postgresql://..." -t -c "SELECT json_agg(t) FROM (SELECT * FROM users LIMIT 10) t;"
```

### Explore Schema

```bash
# List databases
psql "postgresql://..." -c "\l"

# List tables
psql "postgresql://..." -c "\dt"

# Describe table
psql "postgresql://..." -c "\d users"

# List indexes
psql "postgresql://..." -c "\di"
```

---

## MySQL Operations

### Connect and Query

```bash
# Connect and query
mysql -h host -u user -p'password' dbname -e "SELECT * FROM users LIMIT 10;"

# CSV-like output
mysql -h host -u user -p'password' dbname -B -e "SELECT * FROM users;" > output.tsv
```

### Explore Schema

```bash
mysql -h host -u user -p'password' -e "SHOW DATABASES;"
mysql -h host -u user -p'password' dbname -e "SHOW TABLES;"
mysql -h host -u user -p'password' dbname -e "DESCRIBE users;"
```

---

## Common Patterns

### Quick Database Overview

For any SQLite database, run this sequence:
```bash
# 1. List tables
sqlite3 -header -column DB ".tables"
# 2. For each table, show schema + row count
sqlite3 -header -column DB "PRAGMA table_info(TABLE);"
sqlite3 DB "SELECT COUNT(*) as count FROM TABLE;"
# 3. Sample data
sqlite3 -header -column DB "SELECT * FROM TABLE LIMIT 5;"
```

### Safe Query Execution

```bash
# Always use transactions for mutations
sqlite3 DB "BEGIN; UPDATE users SET active=0 WHERE last_login < '2025-01-01'; COMMIT;"

# Or with rollback on error
sqlite3 DB "BEGIN; DELETE FROM old_logs WHERE created < '2024-01-01'; SELECT changes(); COMMIT;"
```

### Large Result Sets

```bash
# Paginate with LIMIT/OFFSET
sqlite3 -header -csv DB "SELECT * FROM big_table LIMIT 100 OFFSET 0;" > page1.csv

# Stream to file for large exports
sqlite3 -header -csv DB "SELECT * FROM big_table;" > full_export.csv
```

### Create Database from Scratch

```bash
# Create new SQLite database with schema
sqlite3 /path/to/new.db <<'SQL'
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT UNIQUE,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_users_email ON users(email);
SQL
```

---

## Output Formatting

| Flag | Format | Use Case |
|------|--------|----------|
| `-header -column` | Aligned columns | Human reading |
| `-header -csv` | CSV | Export to spreadsheet |
| `-json` | JSON array | Programmatic use |
| `-line` | Key=value lines | Simple parsing |
| `-html` | HTML table | Web display |
| `-separator '\t'` | TSV | Tab-separated |

---

## Rules

1. **Read-only first** — always start with SELECT/PRAGMA to understand the data
   before running mutations
2. **Back up before mutate** — for important databases, `.dump` before UPDATE/DELETE
3. **Use transactions** — wrap mutations in BEGIN/COMMIT
4. **Show results** — always display query results to the user, don't just run silently
5. **Respect file paths** — use absolute paths for database files
6. **Connection strings are sensitive** — never log passwords; if user provides
   credentials, use them but don't echo them back
7. **Large results** — for >100 rows, export to CSV and summarize; don't dump
   everything into the conversation
