#!/usr/bin/env bash
# patch_and_setup.sh
# Purpose: non-destructively prepare, patch and launch the "enhanced-deforum-music-generator" project
# TL;DR: This script creates a virtualenv (or uses venv), installs dependencies (pip/conda/npm where available),
# ensures PYTHONPATH points to project root, applies non-destructive patches to make imports and package layout sane,
# runs quick static checks, and runs the project's smoke tests. It does NOT remove or shorten any existing files.

set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/enhanced-deforum-music-generator"
cd "$PROJECT_ROOT" || exit 1

echo "Project root: $PROJECT_ROOT"

# 1) Create a venv if not present
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment .venv..."
  python3 -m venv .venv
fi
# Activate
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel

# 2) Install Python dependencies: try requirements*.txt, pyproject.toml, setup.py, environment.yml fallback
if [ -f "requirements.txt" ]; then
  echo "Installing requirements.txt..."
  pip install -r requirements.txt || true
fi
if [ -f "requirements-dev.txt" ]; then
  pip install -r requirements-dev.txt || true
fi
if [ -f "pyproject.toml" ]; then
  echo "Installing editable via pyproject (pip)..."
  pip install -e . || true
fi
if [ -f "setup.py" ]; then
  echo "Installing via setup.py..."
  pip install -e . || true
fi
# If conda environment files are present, inform the user rather than forcing conda here.
if [ -f "environment.yml" ] || [ -f "environment-cuda.yml" ] || [ -f "environment-full-cuda.yml" ]; then
  echo "Note: conda environment files detected (environment*.yml). If you prefer conda, run:"
  echo "  conda env create -f environment.yml -n edm"
fi

# 3) Node / npm / pnpm for TSX parts
if [ -f "package.json" ] || ls *.tsx >/dev/null 2>&1; then
  if command -v npm >/dev/null 2>&1; then
    echo "Installing npm packages (if package.json exists)..."
    npm install || true
  elif command -v pnpm >/dev/null 2>&1; then
    pnpm install || true
  else
    echo "No npm/pnpm detected. If you need to build TypeScript/UI parts, please install Node/npm or pnpm."
  fi
fi

# 4) Ensure package layout is importable: add __init__.py where missing in top-level folders that look like packages
python - <<'PY'
import os
root = os.getcwd()
# Look for directories with .py files but no __init__.py and add harmless file to make them packages
candidates = []
for d,_,files in os.walk(root):
    if d.endswith('node_modules') or '.git' in d or 'build' in d:
        continue
    py_files = [f for f in files if f.endswith('.py')]
    if py_files and '__init__.py' not in files:
        # only add if directory seems intended as package (not tests/ or scripts/)
        base = os.path.basename(d).lower()
        if base in ('tests','test','scripts','docs','deployment','data'):
            continue
        candidates.append(d)
for d in candidates:
    init_path = os.path.join(d, '__init__.py')
    if not os.path.exists(init_path):
        print('Adding __init__.py to', d)
        open(init_path, 'w').write('# auto-added to make package importable\n')
print('Package init pass complete.\nDetected %d directories adjusted.'%len(candidates))
PY

# 5) Set PYTHONPATH to include project root at runtime via a small wrapper script, preserving everything
WRAPPER="run_with_env.sh"
cat > "$WRAPPER" <<'SH'
#!/usr/bin/env bash
# wrapper to run Python commands with PYTHONPATH set to project root
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"
if [ "$#" -eq 0 ]; then
  echo "Use: ./run_with_env.sh python -m <module> or ./run_with_env.sh python script.py"
  "$PROJECT_ROOT"/.venv/bin/python -V
else
  exec "${@}"
fi
SH
chmod +x "$WRAPPER"

# 6) Run basic static checks: flake8/ruff/mypy if available, else pip install minimal tools
if ! command -v ruff >/dev/null 2>&1; then
  pip install ruff || true
fi
if ! command -v mypy >/dev/null 2>&1; then
  pip install mypy || true
fi
# Run quick ruff on project (best-effort, don't fail entire script)
set +e
ruff . || true
mypy --ignore-missing-imports . || true
set -e

# 7) Run project's tests if any (pytest)
if python -c "import pkgutil, sys; import os; print('has pytest:', os.path.exists('tests') or pkgutil.find_loader('pytest') is not None)" 2>/dev/null; then
  if [ -d "tests" ]; then
    echo "Running pytest (tests/ present) -- best-effort"
    pip install pytest || true
    pytest -q || true
  fi
fi

# 8) Provide next steps and diagnostics file
DIAG="patch_and_setup_diagnostics.txt"
python - <<'PY' > "$DIAG"
import sys, json, pkgutil, subprocess, os
info = {}
info['python_version']=sys.version
info['installed_packages']=subprocess.getoutput('pip freeze')[:4000]
info['project_root']=os.getcwd()
info['env'] = dict(list(filter(lambda kv: 'PATH' in kv[0] or 'PY' in kv[0], os.environ.items())))
print(json.dumps(info, indent=2))
PY

echo "DONE. Created diagnostics file: $PROJECT_ROOT/$DIAG"
echo "Wrapper created: $PROJECT_ROOT/$WRAPPER (use it to run Python commands with PYTHONPATH set)"

echo "If something still fails, save the diagnostics file and paste it to me. I will propose minimal, non-destructive source patches to fix tracebacks."

exit 0
