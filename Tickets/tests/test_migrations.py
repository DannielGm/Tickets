r"""Migration regression tests for the IT Ticket System.

Run with:  .venv\Scripts\python.exe tests\test_migrations.py

Proves that the versioned migration framework in app.py brings both a brand new
database and a pre-existing v0 database to the same schema, that it is
idempotent, and that a failing step rolls back cleanly instead of leaving a
half-migrated file behind.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

# Point the app at a throwaway database *before* importing it: app.py runs
# init_db() at import time, and this keeps the real database.db untouched.
tmpdir = tempfile.mkdtemp(prefix='tickets-migration-')
os.environ['TICKETS_DATABASE'] = os.path.join(tmpdir, 'configured.db')

import app as app_module  # noqa: E402

results = []


def check(label, condition, extra=''):
    results.append(('PASS' if condition else 'FAIL', label, extra))


def columns_of(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def tables_of(conn):
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )}


def indexes_of(conn):
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
    )}


def schema_signature(conn):
    """Full stored schema: compared between the fresh and upgraded databases."""
    return conn.execute('SELECT type, name, sql FROM sqlite_master ORDER BY type, name').fetchall()


def make_legacy_v0_db(path):
    """Build a database exactly as the pre-migration app would have left it."""
    conn = sqlite3.connect(path)
    conn.executescript(app_module.BASELINE_SQL)
    conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('legacyuser', 'x', 'user')")
    conn.execute("INSERT INTO tickets (title, description, created_by) VALUES ('Legacy ticket', 'old', 1)")
    conn.commit()
    return conn


# --- 1. Importing the app already migrated the configured database ----------
conn = sqlite3.connect(app_module.DATABASE)
check('configured database is at SCHEMA_VERSION',
      conn.execute('PRAGMA user_version').fetchone()[0] == app_module.SCHEMA_VERSION)
conn.close()

original_database = app_module.DATABASE
original_migrations = dict(app_module.MIGRATIONS)

try:
    # --- 2. Brand new database ----------------------------------------------
    fresh_path = os.path.join(tmpdir, 'fresh.db')
    app_module.DATABASE = fresh_path
    app_module.init_db()

    conn = sqlite3.connect(fresh_path)
    check('fresh: user_version == 2', conn.execute('PRAGMA user_version').fetchone()[0] == 2)
    check('fresh: tickets gained priority/category/resolved_at',
          {'priority', 'category', 'resolved_at'} <= columns_of(conn, 'tickets'))
    check('fresh: ticket_comments table created', 'ticket_comments' in tables_of(conn))
    check('fresh: indexes created',
          {'idx_tickets_status', 'idx_tickets_created', 'idx_tickets_creator',
           'idx_tickets_assignee', 'idx_comments_ticket'} <= indexes_of(conn))
    check('fresh: demo accounts seeded',
          conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 2)
    fresh_signature = schema_signature(conn)
    conn.close()

    # --- 3. Upgrade of a pre-existing v0 database that already holds data ----
    legacy_path = os.path.join(tmpdir, 'legacy.db')
    conn = make_legacy_v0_db(legacy_path)
    check('legacy: starts at user_version 0', conn.execute('PRAGMA user_version').fetchone()[0] == 0)
    check('legacy: v0 tickets has no priority yet', 'priority' not in columns_of(conn, 'tickets'))
    conn.close()

    app_module.DATABASE = legacy_path
    app_module.init_db()

    conn = sqlite3.connect(legacy_path)
    check('legacy: upgraded to user_version 2', conn.execute('PRAGMA user_version').fetchone()[0] == 2)
    check('legacy: new columns added',
          {'priority', 'category', 'resolved_at'} <= columns_of(conn, 'tickets'))
    check('legacy: ticket_comments added', 'ticket_comments' in tables_of(conn))
    row = conn.execute(
        "SELECT title, priority, category FROM tickets WHERE title = 'Legacy ticket'").fetchone()
    check('legacy: existing row preserved', row is not None and row[0] == 'Legacy ticket', str(row))
    check('legacy: priority backfilled with the default', row[1] == 'medium', str(row))
    check('legacy: category backfilled with the default', row[2] == 'other', str(row))
    check('legacy: existing user preserved',
          conn.execute("SELECT COUNT(*) FROM users WHERE username = 'legacyuser'").fetchone()[0] == 1)
    check('legacy: demo accounts appended',
          conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 3)
    legacy_signature = schema_signature(conn)
    conn.close()

    # --- 4. Both paths must converge on an identical schema ------------------
    check('fresh and upgraded schemas are identical', fresh_signature == legacy_signature,
          'schemas diverged')

    # --- 5. Idempotency -----------------------------------------------------
    for _ in range(3):
        app_module.init_db()
    conn = sqlite3.connect(legacy_path)
    check('re-running init_db keeps version 2', conn.execute('PRAGMA user_version').fetchone()[0] == 2)
    check('re-running init_db does not duplicate users',
          conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 3)
    check('re-running init_db leaves the schema identical',
          schema_signature(conn) == fresh_signature)
    conn.close()

    # --- 6. A failing step must roll back -----------------------------------
    fail_path = os.path.join(tmpdir, 'fail.db')
    app_module.DATABASE = fail_path

    def _broken_step(conn):
        conn.execute('ALTER TABLE tickets ADD COLUMN should_rollback TEXT')
        raise RuntimeError('injected migration failure')

    app_module.MIGRATIONS = {1: _broken_step}
    raised = False
    try:
        app_module.init_db()
    except RuntimeError:
        raised = True
    check('failing migration propagates the error', raised)

    conn = sqlite3.connect(fail_path)
    check('failing migration left the version unstamped',
          conn.execute('PRAGMA user_version').fetchone()[0] == 0)
    check('failing migration rolled the column back',
          'should_rollback' not in columns_of(conn, 'tickets'))
    check('failing migration skipped seeding',
          conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0)
    conn.close()

    # --- 7. Constraints and cascade still work on a migrated database --------
    app_module.MIGRATIONS = original_migrations
    app_module.DATABASE = fresh_path
    conn = app_module.get_db()
    check('PRAGMA foreign_keys is on for app connections',
          conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1)

    try:
        conn.execute("INSERT INTO tickets (title, description, created_by, priority) "
                     "VALUES ('bad', 'bad', 1, 'urgentissimo')")
        conn.commit()
        check('CHECK rejects an invalid priority', False, 'insert unexpectedly succeeded')
    except sqlite3.IntegrityError:
        check('CHECK rejects an invalid priority', True)

    try:
        conn.execute("INSERT INTO tickets (title, description, created_by) VALUES ('o', 'o', 999999)")
        conn.commit()
        check('foreign key rejects an orphan ticket', False, 'insert unexpectedly succeeded')
    except sqlite3.IntegrityError:
        check('foreign key rejects an orphan ticket', True)

    conn.execute("INSERT INTO tickets (title, description, created_by) VALUES ('cascade', 'c', 1)")
    ticket_id = conn.execute("SELECT id FROM tickets WHERE title = 'cascade'").fetchone()[0]
    conn.execute('INSERT INTO ticket_comments (ticket_id, author_id, body) VALUES (?, 1, ?)',
                 (ticket_id, 'hola'))
    conn.commit()
    check('comment row inserted',
          conn.execute('SELECT COUNT(*) FROM ticket_comments').fetchone()[0] == 1)
    conn.execute('DELETE FROM tickets WHERE id = ?', (ticket_id,))
    conn.commit()
    check('deleting a ticket cascades to its comments',
          conn.execute('SELECT COUNT(*) FROM ticket_comments').fetchone()[0] == 0)
    conn.close()

finally:
    app_module.DATABASE = original_database
    app_module.MIGRATIONS = original_migrations
    shutil.rmtree(tmpdir, ignore_errors=True)

for status, label, *extra in results:
    line = f'{status}: {label}'
    if extra and extra[0]:
        line += f'  [{extra[0]}]'
    print(line)

failed = [r for r in results if r[0] == 'FAIL']
print()
print(f'TOTAL={len(results)} PASSED={len(results) - len(failed)} FAILED={len(failed)}')
sys.exit(1 if failed else 0)