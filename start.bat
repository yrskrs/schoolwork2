@echo off
chcp 65001 > nul
title Шкільні Завдання (SchoolNet) — Запуск сервера

:: ── Визначаємо директорію скрипта щоб запускати з будь-якого місця ──────────
cd /d "%~dp0"

:: 1. Перевірка наявності Python
python --version >nul 2>&1
if errorlevel 1 (
    py --version >nul 2>&1
    if errorlevel 1 (
        echo ============================================================
        echo  [ПОМИЛКА] Python не знайдено в системі!
        echo  Будь ласка, встановіть Python 3.10+ з https://www.python.org/
        echo  Обов'язково поставте галочку "Add Python to PATH"!
        echo ============================================================
        pause
        exit /b 1
    )
    set PY_CMD=py
) else (
    set PY_CMD=python
)

:: 2. Перевірка та створення віртуального середовища venv
if not exist "venv\Scripts\python.exe" (
    echo ============================================================
    echo    Шкільні Завдання (SchoolNet) — Первинне налаштування
    echo ============================================================
    echo.
    echo [1/3] Створення віртуального середовища (venv)...
    %PY_CMD% -m venv venv
    if errorlevel 1 (
        echo [ПОМИЛКА] Не вдалося створити venv!
        pause
        exit /b 1
    )
    echo [OK] Віртуальне середовище створено.
    echo.

    echo [2/3] Встановлення бібліотек (Django, PyQt6 тощо)...
    venv\Scripts\python.exe -m pip install --upgrade pip --quiet
    venv\Scripts\pip.exe install -r requirements.txt
    if errorlevel 1 (
        echo [ПОМИЛКА] Не вдалося встановити бібліотеки з requirements.txt!
        pause
        exit /b 1
    )
    echo [OK] Бібліотеки встановлено.
    echo.

    echo [3/3] Перевірка та застосування міграцій бази даних...
    venv\Scripts\python.exe manage.py migrate --no-input >nul 2>&1
    if errorlevel 1 (
        echo [ПОПЕРЕДЖЕННЯ] Міграції завершились з помилкою. Перевірте базу вручну.
    ) else (
        echo [OK] База даних готова.
    )
    echo.
)

:: 3. Запуск графічного інтерфейсу лаунчера без консольного вікна
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" launcher.py
) else (
    start "" "venv\Scripts\python.exe" launcher.py
)

:: Закриваємо чорне вікно консолі одразу після запуску
exit /b 0
