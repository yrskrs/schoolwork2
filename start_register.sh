#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# Скрипт запуску графічного інтерфейсу реєстрації та керування користувачами SchoolNet (Linux / macOS)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Вимикаємо попередження пісочниці Qt/Chromium та Wayland у Linux
export QTWEBENGINE_DISABLE_SANDBOX=1
export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu-sandbox --disable-logging"
export QT_LOGGING_RULES="*.debug=false;qt.webenginecontext.debug=false;qt.qpa.*=false;qt.qpa.wayland*=false;qt.qpa.wayland.textinput*=false"
export PYTHONWARNINGS="ignore"

# 1. Перевірка Python
if command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CMD="python"
else
    echo "❌ [ПОМИЛКА] Python 3 не знайдено в системі!"
    echo "Будь ласка, встановіть Python 3 (sudo apt install python3 python3-venv python3-pip)"
    exit 1
fi

# 2. Перевірка та створення venv
if [ ! -f "$SCRIPT_DIR/venv/bin/python" ]; then
    echo "========================================================"
    echo "   SchoolNet — Реєстрація користувачів (Налаштування)"
    echo "========================================================"
    echo ""
    echo "📦 [1/3] Створення віртуального середовища (venv)..."
    $PY_CMD -m venv "$SCRIPT_DIR/venv" || {
        echo "❌ [ПОМИЛКА] Не вдалося створити venv. Встановіть пакет python3-venv:"
        echo "   sudo apt install python3-venv (або відповідний пакет вашого дистрибутиву)"
        exit 1
    }
    echo "✅ Віртуальне середовище створено."
    echo ""

    echo "📥 [2/3] Встановлення бібліотек (PyQt6, Django тощо)..."
    "$SCRIPT_DIR/venv/bin/python" -m pip install --upgrade pip --quiet
    "$SCRIPT_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
    echo "✅ Всі бібліотеки успішно встановлено."
    echo ""

    echo "🗄️ [3/3] Перевірка бази даних..."
    "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/manage.py" migrate --no-input >/dev/null 2>&1 || true
fi

# 3. Запуск графічного інтерфейсу реєстрації користувачів
exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/user_manager.py" "$@"
