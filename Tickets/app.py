"""
IT Ticket System - A simple web application for basic users to submit IT
problem tickets and IT users to manage and resolve them.

Features:
- Role-based login (basic user / IT user)
- Pre-seeded demo accounts
- Submit, view, and track IT tickets
- IT users can update ticket status and manage user accounts
- SQLite database for persistence
"""

import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Use an absolute path for the database so it works regardless of CWD.
# TICKETS_DATABASE lets a test run point the app at a throwaway file without
# touching the default database.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.environ.get('TICKETS_DATABASE') or os.path.join(BASE_DIR, 'database.db')


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------
# Stored values stay in English ('open', 'it', ...); only the labels shown to
# people are translated here. Adding or changing a label therefore never
# requires a schema migration.

STATUS_LABELS = {
    'open': 'Abierto',
    'in_progress': 'En progreso',
    'finished': 'Finalizado',
}

ROLE_LABELS = {
    'user': 'Usuario',
    'it': 'TI',
}


@app.template_filter('status_label')
def status_label(value):
    """Spanish label for a stored ticket status."""
    return STATUS_LABELS.get(value, value)


@app.template_filter('role_label')
def role_label(value):
    """Spanish label for a stored user role."""
    return ROLE_LABELS.get(value, value)


@app.template_filter('date_label')
def date_label(value):
    """Format a stored timestamp for display in Spanish order.

    SQLite's CURRENT_TIMESTAMP is UTC, so the zone is labelled rather than
    silently presented as local time.
    """
    if not value:
        return ''
    try:
        stamp = datetime.strptime(str(value), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return str(value)
    return stamp.strftime('%d/%m/%Y %H:%M') + ' UTC'


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    """Return a SQLite connection with Row factory enabled.

    SQLite does not enforce foreign keys unless they are switched on for each
    connection, so enable them here to make the schema's FOREIGN KEY clauses
    (tickets.created_by / tickets.assigned_to) actually take effect.
    """
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2

# The schema every database starts from. Keep this frozen: later changes are
# applied by the migration steps below, never by editing this baseline.
# CREATE TABLE IF NOT EXISTS is a no-op on an existing database, and re-running
# an ALTER TABLE ... ADD COLUMN fails with "duplicate column name", so this must
# stay the lowest common denominator for both new and pre-existing DB files.
BASELINE_SQL = '''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            role          TEXT    NOT NULL DEFAULT 'user',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tickets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL,
            description TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'open',
            created_by  INTEGER NOT NULL,
            assigned_to INTEGER,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by)  REFERENCES users (id),
            FOREIGN KEY (assigned_to) REFERENCES users (id)
        );
    '''


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------

def _migrate_to_1(conn):
    """Add priority/category/resolved_at to tickets and index the hot columns."""
    conn.execute(
        "ALTER TABLE tickets ADD COLUMN priority TEXT NOT NULL "
        "DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high'))"
    )
    conn.execute(
        "ALTER TABLE tickets ADD COLUMN category TEXT NOT NULL "
        "DEFAULT 'other' "
        "CHECK (category IN ('hardware', 'software', 'network', 'access', 'other'))"
    )
    conn.execute('ALTER TABLE tickets ADD COLUMN resolved_at TIMESTAMP')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_tickets_status   ON tickets (status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_tickets_created  ON tickets (created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_tickets_creator  ON tickets (created_by)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_tickets_assignee ON tickets (assigned_to)')


def _migrate_to_2(conn):
    """Create the ticket comment and activity thread."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ticket_comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id  INTEGER NOT NULL,
            author_id  INTEGER NOT NULL,
            body       TEXT,
            kind       TEXT    NOT NULL DEFAULT 'comment',
            old_value  TEXT,
            new_value  TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE,
            FOREIGN KEY (author_id) REFERENCES users (id)
        )
    ''')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_comments_ticket '
        'ON ticket_comments (ticket_id, created_at)'
    )


# Ordered migration steps, keyed by the schema version they produce.
MIGRATIONS = {
    1: _migrate_to_1,
    2: _migrate_to_2,
}


def init_db():
    """Bring the database up to SCHEMA_VERSION, then seed the demo accounts."""
    conn = get_db()
    # Take manual control of transactions. Python's sqlite3 only opens one
    # implicitly for DML, so DDL (ALTER/CREATE) would otherwise run in
    # autocommit mode and could not be rolled back if a step failed.
    conn.isolation_level = None
    try:
        # Idempotent for existing databases; creates the v0 schema for new ones.
        conn.executescript(BASELINE_SQL)

        version = conn.execute('PRAGMA user_version').fetchone()[0]
        for target in sorted(MIGRATIONS):
            if version >= target:
                continue
            # BEGIN IMMEDIATE takes the write lock up front, so two processes
            # starting at once (e.g. the debug reloader) serialise safely.
            conn.execute('BEGIN IMMEDIATE')
            try:
                MIGRATIONS[target](conn)
                conn.execute(f'PRAGMA user_version = {target}')
            except Exception:
                conn.execute('ROLLBACK')  # step undone, version left unstamped
                raise                     # fail loudly instead of running broken
            else:
                conn.execute('COMMIT')    # step and version stamp land together

        _seed_demo_accounts(conn)
    finally:
        conn.close()


def _seed_demo_accounts(conn):
    """Insert the demo accounts, but only if they are not there already."""
    existing = conn.execute('SELECT username FROM users').fetchall()
    existing_usernames = {row['username'] for row in existing}

    if 'itadmin' not in existing_usernames:
        conn.execute(
            'INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
            ('itadmin', generate_password_hash('itpass123'), 'it')
        )
    if 'user1' not in existing_usernames:
        conn.execute(
            'INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
            ('user1', generate_password_hash('password123'), 'user')
        )

    conn.commit()


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def login_required(f):
    """Redirect to login page if user is not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Inicie sesión para continuar.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def it_required(f):
    """Ensure the logged-in user has the 'it' role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Inicie sesión para continuar.', 'error')
            return redirect(url_for('login'))
        if session.get('role') != 'it':
            flash('Acceso denegado. Se requieren permisos de TI.', 'error')
            return redirect(url_for('user_dashboard'))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Redirect to the appropriate dashboard based on role."""
    if 'user_id' in session:
        if session.get('role') == 'it':
            return redirect(url_for('it_dashboard'))
        return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))


# --- Authentication --------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = get_db()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ?', (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Sesión iniciada correctamente.', 'success')
            if user['role'] == 'it':
                return redirect(url_for('it_dashboard'))
            return redirect(url_for('user_dashboard'))

        flash('Usuario o contraseña incorrectos.', 'error')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Ha cerrado sesión.', 'success')
    return redirect(url_for('login'))


# --- Basic User Routes -----------------------------------------------------

# --- Dashboard listing helpers (filter / search / sort / pagination) ---------

PER_PAGE = 10

# Whitelisted ORDER BY clauses: the sort parameter never reaches SQL raw.
SORTS = {
    'newest': 't.created_at DESC, t.id DESC',
    'oldest': 't.created_at ASC, t.id ASC',
    'title':  't.title COLLATE NOCASE ASC, t.id ASC',
}


def _like_pattern(q):
    """Escape LIKE wildcards so the user's text is matched literally."""
    return q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _search_clause(columns):
    """WHERE fragment matching any of the given columns, plus its arity."""
    clause = ' OR '.join(f"{c} LIKE ? ESCAPE '\\'" for c in columns)
    return '(' + clause + ')', len(columns)


def _parse_dashboard_args():
    """Read and sanitise the query-string parameters shared by both dashboards."""
    f_status = request.args.get('status', '').strip() or None
    if f_status not in STATUS_LABELS:
        f_status = None
    q = request.args.get('q', '').strip()
    sort = request.args.get('sort', 'newest')
    if sort not in SORTS:
        sort = 'newest'
    try:
        page = max(1, int(request.args.get('page', '1')))
    except ValueError:
        page = 1
    return f_status, q, sort, page


def _listing_context(conn, base_where, base_args, search_columns):
    """Build everything the dashboard templates expect.

    The stat cards count the current search result set but ignore the status
    filter itself, so they act as a drill-down into the filtered list.
    """
    f_status, q, sort, page = _parse_dashboard_args()

    where = list(base_where)
    args = list(base_args)
    if q:
        clause, n = _search_clause(search_columns)
        # _like_pattern only escapes the user's % _ \ characters; the wildcards
        # that make LIKE match anywhere in the value are added here.
        pat = f"%{_like_pattern(q)}%"
        where.append(clause)
        args.extend([pat] * n)
    if f_status:
        where.append('t.status = ?')
        args.append(f_status)

    where_sql = ' WHERE ' + ' AND '.join(where) if where else ''

    # Counts for the stat cards: search applied, status filter not.
    stat_where, stat_args = list(base_where), list(base_args)
    if q:
        clause, n = _search_clause(search_columns)
        stat_where.append(clause)
        stat_args.extend([f"%{_like_pattern(q)}%"] * n)
    stat_sql = ' WHERE ' + ' AND '.join(stat_where) if stat_where else ''

    # The joins are needed in every query because the search clause may
    # reference u.username. users.id is a primary key, so the LEFT JOINs
    # cannot multiply ticket rows and the counts stay correct.
    join_sql = (' LEFT JOIN users u  ON t.created_by  = u.id'
                ' LEFT JOIN users it ON t.assigned_to = it.id')

    stats = {'total': 0}
    for row in conn.execute(
        f'SELECT t.status, COUNT(*) AS n FROM tickets t{join_sql}{stat_sql}'
        ' GROUP BY t.status',
        stat_args
    ):
        stats[row['status']] = row['n']
        stats['total'] += row['n']

    total = conn.execute(
        f'SELECT COUNT(*) FROM tickets t{join_sql}{where_sql}', args
    ).fetchone()[0]
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)

    tickets = conn.execute(
        'SELECT t.*, u.username AS creator_name, it.username AS assignee_name'
        f' FROM tickets t{join_sql}'
        f'{where_sql} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?',
        args + [PER_PAGE, (page - 1) * PER_PAGE]
    ).fetchall()

    return {
        'tickets': tickets, 'stats': stats, 'f_status': f_status,
        'q': q, 'sort': sort, 'page': page, 'total_pages': total_pages,
    }


@app.route('/user/dashboard')
@login_required
def user_dashboard():
    """Show the basic user's tickets."""
    if session.get('role') != 'user':
        return redirect(url_for('it_dashboard'))

    conn = get_db()
    ctx = _listing_context(
        conn,
        base_where=['t.created_by = ?'],
        base_args=[session['user_id']],
        search_columns=['t.title'],
    )
    conn.close()

    return render_template('user_dashboard.html', **ctx)


@app.route('/ticket/submit', methods=['GET', 'POST'])
@login_required
def submit_ticket():
    """Allow a basic user to submit a new ticket."""
    if session.get('role') != 'user':
        return redirect(url_for('it_dashboard'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()

        if not title or not description:
            flash('El título y la descripción son obligatorios.', 'error')
            return render_template('submit_ticket.html')

        conn = get_db()
        conn.execute(
            'INSERT INTO tickets (title, description, created_by) VALUES (?, ?, ?)',
            (title, description, session['user_id'])
        )
        conn.commit()
        conn.close()

        flash('¡Ticket enviado correctamente!', 'success')
        return redirect(url_for('user_dashboard'))

    return render_template('submit_ticket.html')


# --- IT User Routes --------------------------------------------------------

@app.route('/it/dashboard')
@login_required
@it_required
def it_dashboard():
    """Show all tickets to the IT user."""
    conn = get_db()
    ctx = _listing_context(
        conn,
        base_where=[],
        base_args=[],
        search_columns=['t.title', 'u.username'],
    )
    conn.close()

    return render_template('it_dashboard.html', **ctx)


@app.route('/ticket/<int:ticket_id>')
@login_required
def ticket_detail(ticket_id):
    """Show details of a single ticket.

    Basic users can only see their own tickets.
    IT users can see any ticket.
    """
    conn = get_db()
    ticket = conn.execute('''
        SELECT t.*,
               u.username  AS creator_name,
               it.username AS assignee_name
        FROM tickets t
        LEFT JOIN users u  ON t.created_by  = u.id
        LEFT JOIN users it ON t.assigned_to = it.id
        WHERE t.id = ?
    ''', (ticket_id,)).fetchone()
    conn.close()

    if not ticket:
        flash('Ticket no encontrado.', 'error')
        return redirect(url_for('user_dashboard'))

    # Permission check
    if session.get('role') == 'user' and ticket['created_by'] != session['user_id']:
        flash('Acceso denegado.', 'error')
        return redirect(url_for('user_dashboard'))

    return render_template('ticket_detail.html', ticket=ticket)


@app.route('/ticket/<int:ticket_id>/status', methods=['POST'])
@login_required
@it_required
def update_ticket_status(ticket_id):
    """IT user updates the status of a ticket."""
    new_status = request.form.get('status', '').strip()
    valid_statuses = {'open', 'in_progress', 'finished'}

    if new_status not in valid_statuses:
        flash('Estado no válido.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    conn = get_db()

    existing = conn.execute(
        'SELECT id FROM tickets WHERE id = ?', (ticket_id,)
    ).fetchone()
    if not existing:
        conn.close()
        flash('Ticket no encontrado.', 'error')
        return redirect(url_for('it_dashboard'))

    # Optionally assign the ticket to the IT user who is working on it
    conn.execute(
        '''UPDATE tickets
           SET status = ?,
               assigned_to = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?''',
        (new_status, session['user_id'], ticket_id)
    )
    conn.commit()
    conn.close()

    flash('Estado del ticket actualizado.', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


# --- IT User Management ----------------------------------------------------

@app.route('/it/users')
@login_required
@it_required
def user_management():
    """IT user views and manages all user accounts."""
    conn = get_db()
    users = conn.execute(
        'SELECT * FROM users ORDER BY created_at DESC'
    ).fetchall()
    conn.close()

    return render_template('user_management.html', users=users)


@app.route('/it/users/create', methods=['POST'])
@login_required
@it_required
def create_user():
    """IT user creates a new account."""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'user').strip()

    if not username or not password:
        flash('El usuario y la contraseña son obligatorios.', 'error')
        return redirect(url_for('user_management'))

    if role not in ('user', 'it'):
        role = 'user'

    conn = get_db()
    try:
        conn.execute(
            'INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
            (username, generate_password_hash(password), role)
        )
        conn.commit()
        flash(f'¡Usuario "{username}" creado correctamente!', 'success')
    except sqlite3.IntegrityError:
        flash('Ese usuario ya existe.', 'error')
    finally:
        conn.close()

    return redirect(url_for('user_management'))


@app.route('/it/users/reset_password/<int:user_id>', methods=['POST'])
@login_required
@it_required
def reset_password(user_id):
    """IT user resets a user's password."""
    new_password = request.form.get('new_password', '')

    if not new_password:
        flash('La nueva contraseña es obligatoria.', 'error')
        return redirect(url_for('user_management'))

    conn = get_db()

    existing = conn.execute(
        'SELECT id FROM users WHERE id = ?', (user_id,)
    ).fetchone()
    if not existing:
        conn.close()
        flash('Usuario no encontrado.', 'error')
        return redirect(url_for('user_management'))

    conn.execute(
        'UPDATE users SET password_hash = ? WHERE id = ?',
        (generate_password_hash(new_password), user_id)
    )
    conn.commit()
    conn.close()

    flash('Contraseña restablecida correctamente.', 'success')
    return redirect(url_for('user_management'))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Make sure the schema exists no matter how the app is started: `python app.py`,
# `flask run`, `gunicorn app:app`, or a plain import (e.g. from run_setup.py).
init_db()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
