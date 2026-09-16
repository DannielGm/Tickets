r"""End-to-end tests for the IT Ticket System.

Run with:  .venv\Scripts\python.exe tests\test_app.py

Drives every route through Flask's test client against a throwaway database.
Assertions key off structural markers (status codes, CSS classes such as
flash-error / status-in_progress, ticket titles and usernames) instead of
display copy, so they keep passing when the UI text is translated.
"""
import os
import shutil
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

tmpdir = tempfile.mkdtemp(prefix='tickets-app-')
os.environ['TICKETS_DATABASE'] = os.path.join(tmpdir, 'test.db')

import app as app_module  # noqa: E402

results = []


def check(label, condition, extra=''):
    results.append(('PASS' if condition else 'FAIL', label, extra))


def no_success_flash(html):
    """True when the rendered page carries no success flash message."""
    return b'flash-success' not in html


try:
    app_module.app.config.update(TESTING=True)

    # SQLite timestamps must be explicitly UTC before browser conversion.
    check('timestamps serialize with an explicit UTC zone',
          app_module.iso_date('2026-01-01 00:30:00') == '2026-01-01T00:30:00Z')
    check('empty timestamps remain empty', app_module.iso_date(None) == '')
    check('invalid timestamps are not converted', app_module.iso_date('invalid') == '')
    check('no-JavaScript fallback labels UTC honestly',
          app_module.date_label('2026-01-01 00:30:00') == '01/01/2026 00:30 UTC')

    with app_module.app.test_client() as client:
        # --- authentication --------------------------------------------------
        r = client.get('/user/dashboard')
        check('anonymous is redirected to the login page',
              r.status_code == 302 and '/login' in r.headers['Location'], str(r.status_code))

        r = client.post('/login', data={'username': 'user1', 'password': 'nope'},
                        follow_redirects=True)
        check('a wrong password is rejected', b'flash-error' in r.data)

        r = client.post('/login', data={'username': 'user1', 'password': 'password123'},
                        follow_redirects=True)
        check('basic user can log in', r.status_code == 200, str(r.status_code))
        check('a fresh dashboard shows the empty state', b'empty-state' in r.data)

        # --- basic user workflow ---------------------------------------------
        r = client.post('/ticket/submit',
                        data={'title': 'Printer jam', 'description': 'Line 2 jam'},
                        follow_redirects=True)
        check('submitting a ticket succeeds', b'flash-success' in r.data)
        check('the new ticket is listed', b'Printer jam' in r.data)
        check('the empty state is gone once a ticket exists', b'empty-state' not in r.data)

        r = client.get('/ticket/1')
        check('the owner can open their ticket',
              r.status_code == 200 and b'status-badge' in r.data, str(r.status_code))
        check('the owner sees close/reopen controls, not IT controls',
              b'Cerrar ticket' in r.data and b'name="assignee_id"' not in r.data)

        # --- role separation --------------------------------------------------
        r = client.get('/it/dashboard')
        check('a basic user cannot open the IT dashboard', r.status_code == 302, str(r.status_code))

        client.get('/logout')
        r = client.post('/login', data={'username': 'itadmin', 'password': 'itpass123'},
                        follow_redirects=True)
        check('IT user can log in', r.status_code == 200 and b'Printer jam' in r.data)
        check('the IT dashboard shows who submitted the ticket', b'user1' in r.data)
        check('the IT dashboard shows the assignee column', b'Asignado a' in r.data)

        # --- IT dashboard listings: filter / search / sort --------------------
        r = client.get('/it/dashboard')
        check('the stat cards render on the IT dashboard', b'stats-grid' in r.data)

        r = client.get('/it/dashboard?status=open')
        check('IT can filter the dashboard by status', b'Printer jam' in r.data)

        r = client.get('/it/dashboard?status=finished')
        check('an empty filter result shows the filtered empty state (IT)',
              b'Limpiar filtros' in r.data)

        r = client.get('/it/dashboard?q=user1')
        check('IT search matches the creator username too', b'Printer jam' in r.data)

        r = client.get('/it/dashboard?q=zzz')
        check('IT search with no match shows the empty state', b'Limpiar filtros' in r.data)

        r = client.get('/it/dashboard?sort=title')
        check('sorting by title is accepted',
              r.status_code == 200 and b'Printer jam' in r.data)

        r = client.get('/it/dashboard?sort=priority')
        check('sorting by priority is accepted', r.status_code == 200, str(r.status_code))

        r = client.get('/it/dashboard')
        check('the IT dashboard shows priority badges', b'prio-badge' in r.data)

        r = client.post('/ticket/1/status', data={'status': 'in_progress'},
                        follow_redirects=True)
        check('IT can move a ticket to in progress',
              b'status-in_progress' in r.data and b'flash-success' in r.data)

        # --- Phase 5: priority, assignment, closer rule -----------------------
        r = client.post('/ticket/1/priority', data={'priority': 'high'},
                        follow_redirects=True)
        check('IT can change the priority',
              b'prio-high' in r.data and b'flash-success' in r.data)
        check('the priority change is logged', 'cambió la prioridad'.encode('utf-8') in r.data)

        r = client.post('/it/users/create',
                        data={'username': 'it2', 'password': 'pw222222', 'role': 'it'},
                        follow_redirects=True)
        check('IT can create another IT user', b'it2' in r.data)

        r = client.post('/ticket/1/assign', data={'assignee_id': '999999'},
                        follow_redirects=True)
        check('assigning a non-IT user is rejected', b'flash-error' in r.data)

        r = client.post('/ticket/1/comment', data={'body': 'revisado por TI'},
                        follow_redirects=True)
        check('IT can comment on a ticket',
              b'flash-success' in r.data and b'revisado por TI' in r.data)

        r = client.post('/ticket/1/comment', data={'body': '   '},
                        follow_redirects=True)
        check('an empty comment is rejected', b'flash-error' in r.data)

        client.get('/logout')
        client.post('/login', data={'username': 'it2', 'password': 'pw222222'})
        r = client.post('/ticket/1/status', data={'status': 'finished'},
                        follow_redirects=True)
        check('the IT user who closes the ticket becomes the assignee',
              b'status-finished' in r.data and b'it2' in r.data)
        check('the closer assignment is logged',
              'asignó el ticket a it2'.encode('utf-8') in r.data)

        r = client.post('/ticket/1/status', data={'status': 'bogus'}, follow_redirects=True)
        check('an invalid status value is rejected', b'flash-error' in r.data)

        r = client.post('/ticket/999999/status', data={'status': 'open'},
                        follow_redirects=True)
        check('an unknown ticket reports an error', b'flash-error' in r.data)
        check('an unknown ticket does not report success', no_success_flash(r.data))

        # --- user management --------------------------------------------------
        r = client.get('/it/users')
        check('IT can open user management',
              r.status_code == 200 and b'itadmin' in r.data, str(r.status_code))

        r = client.post('/it/users/create',
                        data={'username': 'otro', 'password': 'pw123456', 'role': 'user'},
                        follow_redirects=True)
        check('IT can create a new user', b'flash-success' in r.data and b'otro' in r.data)

        r = client.post('/it/users/create',
                        data={'username': 'otro', 'password': 'pw123456', 'role': 'user'},
                        follow_redirects=True)
        check('a duplicate username is rejected', b'flash-error' in r.data)

        r = client.post('/it/users/reset_password/999999',
                        data={'new_password': 'abc12345'}, follow_redirects=True)
        check('resetting an unknown user reports an error', b'flash-error' in r.data)
        check('resetting an unknown user does not report success', no_success_flash(r.data))

        # --- ownership isolation ---------------------------------------------
        client.get('/logout')
        client.post('/login', data={'username': 'otro', 'password': 'pw123456'},
                    follow_redirects=True)
        r = client.get('/ticket/1')
        check('another basic user cannot read someone else\'s ticket',
              r.status_code == 302, str(r.status_code))

        r = client.get('/ticket/1', follow_redirects=True)
        check('that denial surfaces as an error flash', b'flash-error' in r.data)

        r = client.get('/it/users')
        check('a basic user cannot reach user management', r.status_code == 302, str(r.status_code))

        r = client.post('/ticket/1/comment', data={'body': 'intruso'},
                        follow_redirects=True)
        check('another basic user cannot comment on someone else\'s ticket',
              b'Acceso denegado' in r.data)

        # --- dashboard listings: filter / search / sort / pagination ----------
        for i in range(1, 12):
            client.post('/ticket/submit',
                        data={'title': f'Ticket de prueba {i:02d}', 'description': 'cuerpo'},
                        follow_redirects=True)

        r = client.get('/user/dashboard')
        check('the stat cards render on the user dashboard', b'stats-grid' in r.data)
        check('pagination appears after PER_PAGE tickets',
              'Página 1 de 2'.encode('utf-8') in r.data)
        check('page 1 omits the oldest ticket', b'>Ticket de prueba 01<' not in r.data)

        r = client.get('/user/dashboard?page=2')
        check('page 2 shows the oldest ticket',
              r.status_code == 200 and b'>Ticket de prueba 01<' in r.data, str(r.status_code))

        r = client.get('/user/dashboard?page=99')
        check('an out-of-range page is clamped', 'Página 2 de 2'.encode('utf-8') in r.data)

        r = client.get('/user/dashboard?page=abc')
        check('a non-numeric page is sanitised', 'Página 1 de 2'.encode('utf-8') in r.data)

        conn = app_module.get_db()
        first_id = conn.execute(
            "SELECT id FROM tickets WHERE title = 'Ticket de prueba 01'").fetchone()[0]
        conn.close()

        r = client.get(f'/ticket/{first_id}')
        check('a ticket submitted without priority defaults to medium',
              b'prio-medium' in r.data)

        r = client.get('/user/dashboard?status=finished')
        check('an empty filter result shows the filtered empty state',
              b'Limpiar filtros' in r.data)

        r = client.get('/user/dashboard?status=bogus')
        check('an invalid status filter is sanitised',
              r.status_code == 200 and b'>Ticket de prueba 11<' in r.data, str(r.status_code))

        r = client.get('/user/dashboard?q=07')
        check('search finds the matching ticket',
              b'>Ticket de prueba 07<' in r.data and b'>Ticket de prueba 11<' not in r.data)

        r = client.get('/user/dashboard?q=%25')
        check('the percent wildcard is matched literally', b'Limpiar filtros' in r.data)

        # --- owner: comments, close/reopen, priority defaults ------------------
        client.get('/logout')
        client.post('/login', data={'username': 'user1', 'password': 'password123'})

        r = client.get('/ticket/1')
        check('the IT-set priority persists on the detail page', b'prio-high' in r.data)

        r = client.post('/ticket/1/comment',
                        data={'body': 'hola desde el dueño'}, follow_redirects=True)
        check('the owner can comment on their ticket',
              b'flash-success' in r.data and 'hola desde el dueño'.encode('utf-8') in r.data)

        r = client.post('/ticket/1/comment', data={'body': '   '},
                        follow_redirects=True)
        check('an empty comment is rejected (owner)', b'flash-error' in r.data)

        r = client.post('/ticket/1/status', data={'status': 'in_progress'},
                        follow_redirects=True)
        check('the owner cannot set the ticket in progress', b'flash-error' in r.data)

        r = client.post('/ticket/1/status', data={'status': 'open'},
                        follow_redirects=True)
        check('the owner can reopen their ticket',
              b'status-open' in r.data and b'Sin asignar' in r.data)
        check('reopening clears the assignment in the log',
              'quitó la asignación'.encode('utf-8') in r.data)

        r = client.post('/ticket/1/status', data={'status': 'finished'},
                        follow_redirects=True)
        check('the owner can close their ticket', b'status-finished' in r.data)
        check('an owner-closed ticket keeps no assignee', b'Sin asignar' in r.data)

        r = client.post('/ticket/submit',
                        data={'title': 'Impresora rota', 'description': 'no imprime',
                              'priority': 'high', 'category': 'network'},
                        follow_redirects=True)
        check('submitting with priority and category works',
              b'flash-success' in r.data)

        conn = app_module.get_db()
        broken_id = conn.execute(
            "SELECT id FROM tickets WHERE title = 'Impresora rota'").fetchone()[0]
        conn.close()
        r = client.get(f'/ticket/{broken_id}')
        check('the submitted priority and category render on the detail',
              b'prio-high' in r.data and b'Red' in r.data and b'cat-chip' in r.data)

        r = client.post('/ticket/submit',
                        data={'title': 'Prioridad rara', 'description': 'x',
                              'priority': 'bogus', 'category': 'other'},
                        follow_redirects=True)
        check('an invalid priority is rejected', b'flash-error' in r.data)

        # --- logout ------------------------------------------------------------
        r = client.get('/logout', follow_redirects=True)
        check('logout lands on the login page', r.status_code == 200, str(r.status_code))
        r = client.get('/user/dashboard')
        check('the session is cleared after logout', r.status_code == 302, str(r.status_code))

finally:
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