"""Check if required dependencies are installed and write results to a file."""
import sys
from importlib.metadata import version as pkg_version

results = []
results.append(f"Python version: {sys.version}")

try:
    import flask
    results.append(f"Flask: {pkg_version('flask')} - OK")
except ImportError:
    results.append("Flask: NOT INSTALLED")

try:
    import werkzeug
    results.append(f"Werkzeug: {pkg_version('werkzeug')} - OK")
except ImportError:
    results.append("Werkzeug: NOT INSTALLED")

with open("deps_check.txt", "w") as f:
    f.write("\n".join(results))

print("\n".join(results))
