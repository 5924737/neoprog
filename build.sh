#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-build.txt

rm -rf build dist ch341a-tool.spec
.venv/bin/pyinstaller --onefile --noconsole --name ch341a-tool --collect-data flet main.py

echo "Готово: dist/ch341a-tool"
