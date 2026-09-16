"""Install dependencies and verify the app can start."""
import subprocess
import sys
import os
from importlib.metadata import version as pkg_version

# Run relative to this script's own location instead of a hard-coded path.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

log = []

# Step 1: Install Flask
log.append("=== Installing Flask ===")
result = subprocess.run(
    [sys.executable, "-m", "pip", "install", "flask", "werkzeug"],
    capture_output=True, text=True
)
log.append(result.stdout)
if result.stderr:
    log.append("STDERR: " + result.stderr)
log.append(f"Return code: {result.returncode}")

# Step 2: Verify Flask is installed
log.append("\n=== Verifying Installation ===")
try:
    import flask
    log.append(f"Flask version: {flask.__version__}")
except ImportError as e:
    log.append(f"Flask import failed: {e}")

try:
    import werkzeug
    log.append(f"Werkzeug version: {pkg_version('werkzeug')}")
except ImportError as e:
    log.append(f"Werkzeug import failed: {e}")

# Step 3: Try importing the app module
log.append("\n=== Testing App Import ===")
try:
    import app as app_module
    log.append("App module imported successfully!")
    log.append(f"Routes: {[rule.rule for rule in app_module.app.url_map.iter_rules()]}")
except Exception as e:
    log.append(f"App import failed: {e}")

# Write log to file
with open("setup_log.txt", "w") as f:
    f.write("\n".join(log))

print("\n".join(log))
