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
        check('no status controls are offered to a basic user', b'status-selector' not in r.data)

        # --- role separation --------------------------------------------------
        r = client.get('/it/dashboard')
        check('a basic user cannot open the IT dashboard', r.status_code == 302, str(r.status_code))

        client.get('/logout')
        r = client.post('/login', data={'username': 'itadmin', 'password': 'itpass123'},
                        follow_redirects=True)
        check('IT user can log in', r.status_code == 200 and b'Printer jam' in r.data)
        check('the IT dashboard shows who submitted the ticket', b'user1' in r.data)
        check('the IT dashboard shows the assignee column', b'Asignado a' in r.data)

        r = client.post('/ticket/1/status', data={'status': 'in_progress'},
                        follow_redirects=True)
        check('IT can move a ticket to in progress',
              b'status-in_progress' in r.data and b'flash-success' in r.data)

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