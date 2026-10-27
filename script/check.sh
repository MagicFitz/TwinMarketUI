#!/usr/bin/env bash
# Run all repository checks from the project root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="${PYTHON:-venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
    echo "Python 环境不可用: $PYTHON" >&2
    exit 2
fi

printf '\n== Core simulation ==\n'
"$PYTHON" -m py_compile Agent.py simulation.py
find runtime trader util script tests -type f -name '*.py' -exec "$PYTHON" -m py_compile {} +
"$PYTHON" -m unittest discover -s tests -v

for shell_file in script/*.sh; do
    bash -n "$shell_file"
done

printf '\n== Market data service ==\n'
find market_service -type f -name '*.py' -exec "$PYTHON" -m py_compile {} +
if rg -n --glob '*.py' '(^|[[:space:]])(from|import)[[:space:]]+(backend|studio)([.[:space:]]|$)' market_service; then
    echo "market_service must not import the simulation backend or studio" >&2
    exit 1
fi
"$PYTHON" -m unittest discover -s market_service/tests -t . -v

printf '\n== Independent backend ==\n'
find backend -type f -name '*.py' -exec "$PYTHON" -m py_compile {} +
if rg -n --glob '*.py' '(^|[[:space:]])(from|import)[[:space:]]+studio([.[:space:]]|$)' backend; then
    echo "backend production code must not import studio.*" >&2
    exit 1
fi
# The backend talks to the market data service over HTTP only; its tests may use the service's fixtures.
if rg -n --glob '*.py' --glob '!backend/tests/**' '(^|[[:space:]])(from|import)[[:space:]]+market_service([.[:space:]]|$)' backend; then
    echo "backend production code must not import market_service.*" >&2
    exit 1
fi
"$PYTHON" -m unittest discover -s backend/tests -t . -v

printf '\n== New frontend ==\n'
(cd frontend && npm run test && npm run build)
