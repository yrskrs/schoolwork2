#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Графічний лаунчер та панель керування сервером SchoolNet на PyQt6.
Підтримує темну та світлу теми, вибір IP та портів, збереження налаштувань сесії,
автоматичний запис та експорт логів, моніторинг онлайн-клієнтів в окремому вікні,
та системний трей.
"""

import sys
import os

# Придушення попереджень Qt пісочниці та Wayland у Linux
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu-sandbox --disable-logging"
os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.webenginecontext.debug=false;qt.qpa.*=false;qt.qpa.wayland*=false;qt.qpa.wayland.textinput*=false"
os.environ["PYTHONWARNINGS"] = "ignore"

import socket
import json
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPlainTextEdit, QGroupBox, QFrame, QSystemTrayIcon, QMenu,
    QMessageBox, QStatusBar, QFileDialog
)
from PyQt6.QtCore import Qt, QProcess, QTimer, QTime, QSettings, QUrl, qInstallMessageHandler
from PyQt6.QtGui import QTextCursor, QColor, QTextCharFormat, QAction, QDesktopServices

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / 'logs'


def qt_suppress_handler(mode, context, message):
    """Приглушує некритичні системні повідомлення Wayland та Chromium Sandbox."""
    msg_low = message.lower()
    if any(k in msg_low for k in ("wayland", "sandbox", "zwp_text_input", "leave event", "user namespace")):
        return
    if mode in (3, 4):
        sys.stderr.write(f"{message}\n")


def get_local_ip_addresses():
    """
    Автоматично знаходить усі доступні IP-адреси мережевих інтерфейсів комп'ютера.
    """
    ips = [('0.0.0.0 (Усі мережеві карти — доступно в усій школі 🌐)', '0.0.0.0')]
    
    # Спроба отримати основну IP через з'єднання з сокетом
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('8.8.8.8', 80))
        main_ip = s.getsockname()[0]
        s.close()
        if main_ip and main_ip != '127.0.0.1':
            ips.append((f'{main_ip} (Основна локальна IP компʼютера)', main_ip))
    except Exception:
        pass

    # Отримуємо всі IP за назвою хоста
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith('127.') and ip != '0.0.0.0':
                label = f'{ip} (Мережевий інтерфейс)'
                if not any(item[1] == ip for item in ips):
                    ips.append((label, ip))
    except Exception:
        pass

    ips.append(('127.0.0.1 (Тільки цей компʼютер — Localhost 🔒)', '127.0.0.1'))
    return ips


class ConnectedClientsDialog(QDialog):
    """
    Окреме модальне вікно для детального перегляду підключених клієнтів (IP-адреси, активність, останні запити).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("👥 Підключені компʼютери онлайн — SchoolNet")
        self.resize(680, 420)
        self.setMinimumSize(540, 320)
        self.parent_launcher = parent

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Шапка діалогу
        header = QHBoxLayout()
        self.title_lbl = QLabel("👥 Активні пристрої в локальній мережі")
        self.title_lbl.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.count_badge = QLabel("Онлайн: 0")
        self.count_badge.setStyleSheet("""
            background: rgba(16, 185, 129, 0.18);
            color: #10b981;
            border: 1px solid #10b981;
            font-weight: 700;
            border-radius: 10px;
            padding: 3px 10px;
        """)
        header.addWidget(self.title_lbl)
        header.addStretch()
        header.addWidget(self.count_badge)
        layout.addLayout(header)

        # Таблиця клієнтів
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["№", "IP-адреса компʼютера", "Остання активність", "Останній перегляд"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        # Нижні кнопки
        bottom = QHBoxLayout()
        btn_copy = QPushButton("📋 Скопіювати всі IP")
        btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_copy.clicked.connect(self.copy_all_ips)

        btn_refresh = QPushButton("🔄 Оновити")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.clicked.connect(self.refresh_data)

        btn_close = QPushButton("Закрити")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.close)

        bottom.addWidget(btn_copy)
        bottom.addStretch()
        bottom.addWidget(btn_refresh)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

        # Таймер автооновлення таблиці
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh_data)
        self.timer.start()

        self.refresh_data()

    def refresh_data(self):
        if not self.parent_launcher:
            return
        
        clients = getattr(self.parent_launcher, 'last_known_clients', [])
        count = len(clients)
        
        self.count_badge.setText(f"Онлайн: {count}")
        self.table.setRowCount(count)

        for row, c in enumerate(clients):
            # №
            it_num = QTableWidgetItem(str(row + 1))
            it_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, it_num)

            # IP
            it_ip = QTableWidgetItem(f"💻 {c.get('ip', '—')}")
            it_ip.setFont(self.font())
            it_ip.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 1, it_ip)

            # Активність
            ago = c.get('last_seen_seconds_ago', 0)
            if ago <= 5:
                ago_str = "🟢 щойно"
            elif ago < 60:
                ago_str = f"🟢 {ago}с тому"
            else:
                ago_str = f"🟡 {ago // 60}хв {ago % 60}с тому"
            
            last_time = c.get('last_time', '')
            if last_time:
                ago_str += f" ({last_time})"

            it_act = QTableWidgetItem(ago_str)
            it_act.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, it_act)

            # Сторінка
            path = c.get('last_path', '/')
            if path == '/':
                path_desc = "Головна стрічка"
            elif path.startswith('/assignment/'):
                path_desc = f"Завдання {path}"
            elif path.startswith('/teacher/'):
                path_desc = f"Панель вчителя {path}"
            else:
                path_desc = path

            it_path = QTableWidgetItem(path_desc)
            self.table.setItem(row, 3, it_path)

    def copy_all_ips(self):
        if not self.parent_launcher:
            return
        clients = getattr(self.parent_launcher, 'last_known_clients', [])
        if not clients:
            QMessageBox.information(self, "Копіювання", "Немає підключених клієнтів.")
            return
        
        ips_text = "\n".join([c.get('ip', '') for c in clients if c.get('ip')])
        QApplication.clipboard().setText(ips_text)
        QMessageBox.information(self, "Успішно скопійовано", f"Скопійовано {len(clients)} IP-адрес у буфер обміну:\n\n{ips_text}")


class SchoolNetLauncher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Шкільні Завдання — Панель керування сервером")
        self.resize(880, 660)
        self.setMinimumSize(740, 540)
        
        self.process = None
        self.settings = QSettings("SchoolNet", "Launcher")
        self.current_theme = self.settings.value("theme", "dark")
        self.current_server_url = ""
        self.last_known_clients = []
        
        # Ініціалізація файлового логування сесії
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        session_time_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.session_log_file = LOGS_DIR / f"server_{session_time_str}.log"
        self.latest_log_file = LOGS_DIR / "latest.log"
        self.errors_log_file = LOGS_DIR / "errors.log"

        self.stats_timer = QTimer(self)
        self.stats_timer.setInterval(2000)
        self.stats_timer.timeout.connect(self.poll_server_stats)
        
        self.init_ui()
        self.init_tray()
        self.load_saved_settings()
        self.apply_theme()
        
        self.log_info("✨ Лаунчер ініціалізовано. Файл сесійного логу: " + str(self.session_log_file.name))

    def init_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(14)

        # ─── 1. Верхня шапка (Заголовок, статус, перемикач теми) ──────────────
        header_card = QFrame()
        header_card.setObjectName("headerCard")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)

        title_box = QVBoxLayout()
        title_lbl = QLabel("🌐 Шкільні Завдання (SchoolNet)")
        title_lbl.setObjectName("appTitle")
        subtitle_lbl = QLabel("Локальний шкільний сервер навчальних завдань • Працює повністю офлайн")
        subtitle_lbl.setObjectName("appSubtitle")
        title_box.addWidget(title_lbl)
        title_box.addWidget(subtitle_lbl)
        header_layout.addLayout(title_box)

        header_layout.addStretch()

        # Перемикач теми
        self.btn_theme_toggle = QPushButton()
        self.btn_theme_toggle.setObjectName("btnTheme")
        self.btn_theme_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme_toggle.clicked.connect(self.toggle_theme)
        self.update_theme_btn_text()
        header_layout.addWidget(self.btn_theme_toggle)

        # Бейдж статусу
        self.status_badge = QLabel("⚪ Сервер зупинено")
        self.status_badge.setObjectName("statusBadge")
        header_layout.addWidget(self.status_badge)

        main_layout.addWidget(header_card)

        # ─── 2. Панель налаштувань мережі ─────────────────────────────────────
        config_group = QGroupBox("⚙️ Мережеві налаштування сервера")
        config_layout = QHBoxLayout(config_group)
        config_layout.setContentsMargins(16, 16, 16, 16)
        config_layout.setSpacing(14)

        # Вибір IP
        ip_label = QLabel("IP-адреса:")
        ip_label.setStyleSheet("font-weight: 600;")
        
        self.ip_combo = QComboBox()
        self.ip_combo.setEditable(True)
        self.ip_combo.setMinimumWidth(300)
        self.ip_combo.setObjectName("ipCombo")
        
        self.available_ips = get_local_ip_addresses()
        for label, ip_val in self.available_ips:
            self.ip_combo.addItem(label, ip_val)

        config_layout.addWidget(ip_label)
        config_layout.addWidget(self.ip_combo, 3)

        # Вибір порту
        port_label = QLabel("Порт:")
        port_label.setStyleSheet("font-weight: 600;")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(8000)
        self.port_spin.setFixedWidth(95)
        self.port_spin.setObjectName("portSpin")

        config_layout.addWidget(port_label)
        config_layout.addWidget(self.port_spin, 1)

        # Кнопка / Бейдж підключених клієнтів онлайн
        self.btn_clients = QPushButton("👥 Клієнтів онлайн: 0 🔍")
        self.btn_clients.setObjectName("clientsBadge")
        self.btn_clients.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clients.setToolTip("Натисніть, щоб відкрити окреме вікно зі списком усіх підключених IP компʼютерів")
        self.btn_clients.clicked.connect(self.open_clients_dialog)
        config_layout.addWidget(self.btn_clients, 2)

        main_layout.addWidget(config_group)

        # ─── 3. Кнопки керування ──────────────────────────────────────────────
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(10)

        self.btn_start = QPushButton("▶ Запустити сервер")
        self.btn_start.setObjectName("btnStart")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self.start_server)

        self.btn_stop = QPushButton("⏹ Зупинити сервер")
        self.btn_stop.setObjectName("btnStop")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_server)

        self.btn_restart = QPushButton("🔄 Перезапустити")
        self.btn_restart.setObjectName("btnRestart")
        self.btn_restart.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart.setEnabled(False)
        self.btn_restart.clicked.connect(self.restart_server)

        self.btn_open_browser = QPushButton("🌐 Відкрити сайт")
        self.btn_open_browser.setObjectName("btnBrowser")
        self.btn_open_browser.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_browser.setEnabled(False)
        self.btn_open_browser.clicked.connect(self.open_in_browser)

        self.btn_copy_link = QPushButton("📋 Скопіювати лінк")
        self.btn_copy_link.setObjectName("btnCopy")
        self.btn_copy_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_link.setEnabled(False)
        self.btn_copy_link.clicked.connect(self.copy_server_link)

        actions_layout.addWidget(self.btn_start, 2)
        actions_layout.addWidget(self.btn_stop, 2)
        actions_layout.addWidget(self.btn_restart, 1)
        actions_layout.addWidget(self.btn_open_browser, 2)
        actions_layout.addWidget(self.btn_copy_link, 2)

        main_layout.addLayout(actions_layout)

        # ─── 4. Термінальний журнал (Live Log) ─────────────────────────────────
        log_group = QGroupBox("📜 Журнал роботи сервера (Live Console Log)")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(12, 12, 12, 12)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logView")
        log_layout.addWidget(self.log_view)

        log_bottom = QHBoxLayout()
        self.url_label = QLabel("Адреса сайту: —")
        self.url_label.setObjectName("urlLabel")
        
        btn_open_teacher = QPushButton("👑 Панель вчителя")
        btn_open_teacher.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open_teacher.clicked.connect(self.open_teacher_panel)

        btn_user_manager = QPushButton("👥 Користувачі / Реєстрація")
        btn_user_manager.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_user_manager.setToolTip("Відкрити вікно реєстрації та керування користувачами")
        btn_user_manager.clicked.connect(self.open_user_manager)

        btn_save_log = QPushButton("💾 Зберегти лог...")
        btn_save_log.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_log.setToolTip("Експортувати весь журнал сесії у файл")
        btn_save_log.clicked.connect(self.save_log_dialog)

        btn_open_logs_dir = QPushButton("📂 Папка логів")
        btn_open_logs_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open_logs_dir.setToolTip("Відкрити папку з автоматично збереженими логами")
        btn_open_logs_dir.clicked.connect(self.open_logs_directory)

        btn_clear_log = QPushButton("🧹 Очистити")
        btn_clear_log.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_log.clicked.connect(self.log_view.clear)

        log_bottom.addWidget(self.url_label, 1)
        log_bottom.addWidget(btn_open_teacher)
        log_bottom.addWidget(btn_user_manager)
        log_bottom.addWidget(btn_save_log)
        log_bottom.addWidget(btn_open_logs_dir)
        log_bottom.addWidget(btn_clear_log)
        log_layout.addLayout(log_bottom)

        main_layout.addWidget(log_group, 1)

        # Статусбар
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Готовий до запуску. Натисніть 'Запустити сервер'")

    def open_clients_dialog(self):
        """Відкриває окреме модальне вікно з переліком усіх підключених IP."""
        dialog = ConnectedClientsDialog(self)
        dialog.exec()

    def init_tray(self):
        """Ініціалізація системного трея."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
            
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        
        tray_menu = QMenu()
        act_show = QAction("Показати вікно", self)
        act_show.triggered.connect(self.showNormal)
        
        act_clients = QAction("Переглянути онлайн-клієнтів", self)
        act_clients.triggered.connect(self.open_clients_dialog)
        
        act_browser = QAction("Відкрити сайт у браузері", self)
        act_browser.triggered.connect(self.open_in_browser)
        
        act_quit = QAction("Вийти", self)
        act_quit.triggered.connect(self.close)
        
        tray_menu.addAction(act_show)
        tray_menu.addAction(act_clients)
        tray_menu.addAction(act_browser)
        tray_menu.addSeparator()
        tray_menu.addAction(act_quit)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def update_theme_btn_text(self):
        if self.current_theme == "dark":
            self.btn_theme_toggle.setText("☀️ Світла тема")
        else:
            self.btn_theme_toggle.setText("🌙 Темна тема")

    def toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self.settings.setValue("theme", self.current_theme)
        self.update_theme_btn_text()
        self.apply_theme()

    def load_saved_settings(self):
        """Відновлює налаштування останньої сесії (IP, порт, геометрія)."""
        saved_ip = self.settings.value("last_ip", "")
        saved_port = self.settings.value("last_port", 8000, type=int)
        
        if saved_port and 1024 <= saved_port <= 65535:
            self.port_spin.setValue(saved_port)
            
        if saved_ip:
            # Шукаємо збережений IP серед доступних
            found_idx = -1
            for idx in range(self.ip_combo.count()):
                val = self.ip_combo.itemData(idx)
                if val == saved_ip or self.ip_combo.itemText(idx).startswith(saved_ip):
                    found_idx = idx
                    break
            if found_idx >= 0:
                self.ip_combo.setCurrentIndex(found_idx)
            else:
                self.ip_combo.setEditText(saved_ip)
        else:
            if len(self.available_ips) > 1 and self.available_ips[1][1] != '127.0.0.1':
                self.ip_combo.setCurrentIndex(1)
            else:
                self.ip_combo.setCurrentIndex(0)

        # Відновлюємо розмір вікна
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def save_current_settings(self):
        """Зберігає поточні налаштування для наступного запуску."""
        ip = self.get_selected_ip()
        port = self.port_spin.value()
        self.settings.setValue("last_ip", ip)
        self.settings.setValue("last_port", port)
        self.settings.setValue("theme", self.current_theme)
        self.settings.setValue("geometry", self.saveGeometry())

    def get_selected_ip(self):
        """Повертає обрану або введену IP-адресу."""
        idx = self.ip_combo.currentIndex()
        if idx >= 0:
            data_val = self.ip_combo.itemData(idx)
            if data_val:
                return data_val
        text = self.ip_combo.currentText().strip()
        if ' ' in text:
            text = text.split(' ')[0].strip()
        return text or '0.0.0.0'

    def get_browser_accessible_url(self):
        """Повертає URL, доступний для відкриття в браузері."""
        ip = self.get_selected_ip()
        port = self.port_spin.value()
        
        if ip == '0.0.0.0':
            if len(self.available_ips) > 1 and self.available_ips[1][1] not in ('0.0.0.0', '127.0.0.1'):
                ip = self.available_ips[1][1]
            else:
                ip = '127.0.0.1'
        return f"http://{ip}:{port}/"

    def start_server(self):
        """Запускає Django development server через QProcess."""
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            return

        self.save_current_settings()

        ip = self.get_selected_ip()
        port = self.port_spin.value()
        bind_addr = f"{ip}:{port}"
        self.current_server_url = self.get_browser_accessible_url()

        # Визначаємо шлях до Python у venv
        venv_python = sys.executable
        if os.name == 'nt':
            candidate = BASE_DIR / 'venv' / 'Scripts' / 'python.exe'
        else:
            candidate = BASE_DIR / 'venv' / 'bin' / 'python'
        if candidate.exists():
            venv_python = str(candidate)

        manage_py = str(BASE_DIR / 'manage.py')

        self.log_info(f"🚀 Запуск сервера на {bind_addr}...")
        self.log_info(f"📂 Робоча директорія: {BASE_DIR}")
        self.log_info(f"🌐 Адреса сайту для учнів: {self.current_server_url}")

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(BASE_DIR))
        self.process.readyReadStandardOutput.connect(self.on_stdout)
        self.process.readyReadStandardError.connect(self.on_stderr)
        self.process.finished.connect(self.on_process_finished)

        args = [manage_py, 'runserver', bind_addr]
        self.process.start(venv_python, args)

        # Оновлення інтерфейсу
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_restart.setEnabled(True)
        self.btn_open_browser.setEnabled(True)
        self.btn_copy_link.setEnabled(True)
        self.ip_combo.setEnabled(False)
        self.port_spin.setEnabled(False)

        self.status_badge.setText("🟢 Сервер працює")
        self.status_badge.setStyleSheet("""
            background: rgba(16, 185, 129, 0.18);
            color: #059669;
            border: 1px solid #10b981;
            font-weight: 700;
            border-radius: 12px;
            padding: 5px 14px;
        """)
        
        self.url_label.setText(f"Адреса сайту: <a href='{self.current_server_url}'>{self.current_server_url}</a>")
        self.url_label.setOpenExternalLinks(True)
        self.status_bar.showMessage(f"Сервер успішно запущено на {self.current_server_url}")

        self.stats_timer.start()
        QTimer.singleShot(1000, self.poll_server_stats)

    def stop_server(self):
        """Зупиняє запущений сервер."""
        if not self.process:
            return

        self.log_info("⏹ Зупинка сервера...")
        self.stats_timer.stop()

        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1500):
                self.process.kill()

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_restart.setEnabled(False)
        self.btn_open_browser.setEnabled(False)
        self.btn_copy_link.setEnabled(False)
        self.ip_combo.setEnabled(True)
        self.port_spin.setEnabled(True)

        self.status_badge.setText("⚪ Сервер зупинено")
        self.status_badge.setStyleSheet("""
            background: rgba(100, 116, 139, 0.15);
            color: #64748b;
            border: 1px solid #94a3b8;
            font-weight: 700;
            border-radius: 12px;
            padding: 5px 14px;
        """)

        self.btn_clients.setText("👥 Клієнтів онлайн: 0 🔍")
        self.last_known_clients = []
        self.url_label.setText("Адреса сайту: —")
        self.status_bar.showMessage("Сервер зупинено.")

    def restart_server(self):
        """Перезапускає сервер."""
        self.stop_server()
        QTimer.singleShot(800, self.start_server)

    def on_stdout(self):
        if not self.process:
            return
        data = self.process.readAllStandardOutput().data().decode('utf-8', errors='replace')
        self.append_log(data)

    def on_stderr(self):
        if not self.process:
            return
        data = self.process.readAllStandardError().data().decode('utf-8', errors='replace')
        self.append_log(data, is_err=True)

    def on_process_finished(self, exit_code, exit_status):
        self.log_info(f"🛑 Процес сервера завершився з кодом {exit_code}")
        self.stop_server()

    def append_log(self, text, is_err=False):
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        
        fmt = QTextCharFormat()
        is_dark = (self.current_theme == "dark")

        has_error = is_err and ('Error' in text or 'Traceback' in text or 'HTTP/1.1" 500' in text or 'Exception' in text or '❌' in text)
        if has_error:
            fmt.setForeground(QColor('#ef4444'))
        elif 'HTTP/1.1" 200' in text or '200 OK' in text:
            fmt.setForeground(QColor('#10b981'))
        elif 'HTTP/1.1" 404' in text or 'HTTP/1.1" 304' in text or '⚠️' in text:
            fmt.setForeground(QColor('#f59e0b'))
        else:
            fmt.setForeground(QColor('#e2e8f0' if is_dark else '#1e293b'))

        cursor.insertText(text, fmt)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

        # Автоматичний запис у файл логу сесії
        try:
            with open(self.session_log_file, 'a', encoding='utf-8') as f:
                f.write(text)
            with open(self.latest_log_file, 'a', encoding='utf-8') as f:
                f.write(text)
            if has_error:
                with open(self.errors_log_file, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
        except Exception:
            pass

    def log_info(self, msg):
        self.append_log(f"[{QTime.currentTime().toString('HH:mm:ss')}] {msg}\n")

    def poll_server_stats(self):
        """
        Опитує локальний серверний API для отримання кількості та списку онлайн-клієнтів.
        """
        port = self.port_spin.value()
        selected_ip = self.get_selected_ip()
        
        candidate_hosts = ['127.0.0.1', 'localhost']
        if selected_ip not in ('0.0.0.0', '127.0.0.1', 'localhost'):
            candidate_hosts.insert(0, selected_ip)

        for host in candidate_hosts:
            stats_url = f"http://{host}:{port}/api/server-stats/"
            try:
                req = urllib.request.Request(stats_url, headers={'User-Agent': 'SchoolNet-Launcher/1.0'})
                with urllib.request.urlopen(req, timeout=1.2) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode('utf-8'))
                        count = data.get('online_count', 0)
                        clients = data.get('clients', [])
                        self.last_known_clients = clients
                        
                        self.btn_clients.setText(f"👥 Клієнтів онлайн: {count} 🔍")
                        
                        if clients:
                            tooltip_lines = [f"Активні пристрої онлайн ({count}) — клікніть для перегляду списку:"]
                            for c in clients[:10]:
                                ago = c.get('last_seen_seconds_ago', 0)
                                ago_str = f"{ago}с тому" if ago < 60 else f"{ago//60}хв тому"
                                tooltip_lines.append(f" • {c.get('ip')} ({ago_str})")
                            if len(clients) > 10:
                                tooltip_lines.append(f"... та ще {len(clients)-10} пристроїв")
                            self.btn_clients.setToolTip("\n".join(tooltip_lines))
                        else:
                            self.btn_clients.setToolTip("Клієнти ще не підключились до сервера")
                        return
            except Exception:
                continue

    def save_log_dialog(self):
        """Зберігає весь журнал роботи сервера у вибраний користувачем файл."""
        default_name = f"schoolnet_log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
        default_path = str(LOGS_DIR / default_name)
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Зберегти журнал сервера",
            default_path,
            "Текстові файли логів (*.log *.txt);;Усі файли (*.*)"
        )
        if file_path:
            try:
                content = self.log_view.toPlainText()
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.status_bar.showMessage(f"✅ Лог успішно збережено: {file_path}", 5000)
                QMessageBox.information(self, "Лог збережено", f"Журнал сервера збережено у файл:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Помилка збереження", f"Не вдалося зберегти файл: {str(e)}")

    def open_logs_directory(self):
        """Відкриває папку з логами у системному файловому менеджері."""
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOGS_DIR.resolve())))
        except Exception as e:
            self.status_bar.showMessage(f"Не вдалося відкрити папку: {str(e)}", 4000)

    def open_in_browser(self):
        url = self.current_server_url or self.get_browser_accessible_url()
        webbrowser.open(url)

    def open_teacher_panel(self):
        url = self.current_server_url or self.get_browser_accessible_url()
        webbrowser.open(url.rstrip('/') + '/teacher/')

    def open_user_manager(self):
        """Відкриває графічне вікно реєстрації та керування користувачами."""
        try:
            from user_manager import UserManagerWindow
            if not hasattr(self, '_user_manager_win') or self._user_manager_win is None:
                self._user_manager_win = UserManagerWindow(self)
            self._user_manager_win.refresh_users_table()
            self._user_manager_win.show()
            self._user_manager_win.raise_()
            self._user_manager_win.activateWindow()
        except Exception as e:
            QMessageBox.critical(self, "Помилка", f"Не вдалося відкрити вікно користувачів:\n{e}")

    def copy_server_link(self):
        url = self.current_server_url or self.get_browser_accessible_url()
        cb = QApplication.clipboard()
        cb.setText(url)
        self.status_bar.showMessage(f"✅ Посилання скопійовано: {url}", 4000)

    def closeEvent(self, event):
        """Збереження налаштувань та коректна зупинка сервера при закритті вікна."""
        self.save_current_settings()
        
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            reply = QMessageBox.question(
                self,
                'Підтвердження закриття',
                'Сервер зараз працює. Зупинити сервер та закрити програму?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.stop_server()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def apply_theme(self):
        """Застосовує обрану тему (темну або світлу)."""
        if self.current_theme == "light":
            self.apply_light_theme()
        else:
            self.apply_dark_theme()

    def apply_dark_theme(self):
        """Сучасна темна тема з індиго-акцентами."""
        self.setStyleSheet("""
            QMainWindow, QWidget, QDialog {
                background-color: #0f172a;
                color: #f8fafc;
                font-family: 'Segoe UI', system-ui, -apple-system, Roboto, sans-serif;
                font-size: 13px;
            }
            #headerCard {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 10px;
            }
            #appTitle {
                font-size: 16px;
                font-weight: 700;
                color: #ffffff;
            }
            #appSubtitle {
                font-size: 12px;
                color: #94a3b8;
            }
            #statusBadge {
                background: rgba(100, 116, 139, 0.18);
                color: #94a3b8;
                border: 1px solid #475569;
                border-radius: 12px;
                padding: 5px 14px;
                font-weight: 700;
            }
            #clientsBadge {
                background: rgba(99, 102, 241, 0.18);
                color: #a5b4fc;
                border: 1px solid #6366f1;
                border-radius: 7px;
                padding: 6px 12px;
                font-weight: 700;
                font-size: 12.5px;
            }
            #clientsBadge:hover {
                background: rgba(99, 102, 241, 0.35);
                border-color: #818cf8;
            }
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 10px;
                margin-top: 8px;
                padding-top: 16px;
                font-weight: 700;
                color: #cbd5e1;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QComboBox, QSpinBox {
                background-color: #1e293b;
                border: 1px solid #475569;
                border-radius: 7px;
                padding: 6px 10px;
                color: #ffffff;
                selection-background-color: #6366f1;
            }
            QComboBox:hover, QSpinBox:hover {
                border-color: #6366f1;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 26px;
                border-left: 1px solid #475569;
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
                background: #334155;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #ffffff;
                margin: auto;
            }
            QComboBox QAbstractItemView {
                background-color: #1e293b;
                border: 1px solid #475569;
                color: #ffffff;
                selection-background-color: #4f46e5;
                selection-color: #ffffff;
                padding: 4px;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                min-height: 28px;
                padding: 4px 8px;
                border-radius: 4px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #334155;
            }
            QTableWidget {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                gridline-color: #334155;
                color: #f8fafc;
                selection-background-color: #4f46e5;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                font-weight: 700;
                padding: 6px;
                border: 1px solid #334155;
            }
            QPushButton {
                background-color: #334155;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 7px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #475569;
                border-color: #64748b;
            }
            QPushButton:disabled {
                background-color: #1e293b;
                color: #64748b;
                border-color: #334155;
            }
            #btnTheme {
                background-color: #1e293b;
                border: 1px solid #475569;
                color: #f8fafc;
                padding: 5px 12px;
                font-size: 12px;
            }
            #btnTheme:hover {
                background-color: #334155;
                border-color: #6366f1;
            }
            #btnStart {
                background-color: #059669;
                color: #ffffff;
                border: 1px solid #10b981;
            }
            #btnStart:hover {
                background-color: #10b981;
            }
            #btnStop {
                background-color: #dc2626;
                color: #ffffff;
                border: 1px solid #ef4444;
            }
            #btnStop:hover {
                background-color: #ef4444;
            }
            #btnBrowser {
                background-color: #4f46e5;
                color: #ffffff;
                border: 1px solid #6366f1;
            }
            #btnBrowser:hover {
                background-color: #6366f1;
            }
            #logView {
                background-color: #020617;
                color: #e2e8f0;
                border: 1px solid #1e293b;
                border-radius: 8px;
                font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                line-height: 1.4;
            }
            QStatusBar {
                color: #94a3b8;
                font-size: 11.5px;
            }
            #urlLabel {
                font-weight: 600;
                color: #38bdf8;
            }
        """)

    def apply_light_theme(self):
        """Світла сучасна тема з чистими кольорами."""
        self.setStyleSheet("""
            QMainWindow, QWidget, QDialog {
                background-color: #f1f5f9;
                color: #0f172a;
                font-family: 'Segoe UI', system-ui, -apple-system, Roboto, sans-serif;
                font-size: 13px;
            }
            #headerCard {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 10px;
            }
            #appTitle {
                font-size: 16px;
                font-weight: 700;
                color: #0f172a;
            }
            #appSubtitle {
                font-size: 12px;
                color: #64748b;
            }
            #statusBadge {
                background: #f1f5f9;
                color: #64748b;
                border: 1px solid #cbd5e1;
                border-radius: 12px;
                padding: 5px 14px;
                font-weight: 700;
            }
            #clientsBadge {
                background: #eff6ff;
                color: #2563eb;
                border: 1px solid #bfdbfe;
                border-radius: 7px;
                padding: 6px 12px;
                font-weight: 700;
                font-size: 12.5px;
            }
            #clientsBadge:hover {
                background: #dbeafe;
                border-color: #3b82f6;
            }
            QGroupBox {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                margin-top: 8px;
                padding-top: 16px;
                font-weight: 700;
                color: #334155;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QComboBox, QSpinBox {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                padding: 6px 10px;
                color: #0f172a;
                selection-background-color: #3b82f6;
            }
            QComboBox:hover, QSpinBox:hover {
                border-color: #3b82f6;
                background-color: #ffffff;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 26px;
                border-left: 1px solid #cbd5e1;
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
                background: #e2e8f0;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #334155;
                margin: auto;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                color: #0f172a;
                selection-background-color: #3b82f6;
                selection-color: #ffffff;
                padding: 4px;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                min-height: 28px;
                padding: 4px 8px;
                border-radius: 4px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #f1f5f9;
            }
            QTableWidget {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                gridline-color: #f1f5f9;
                color: #0f172a;
                selection-background-color: #dbeafe;
                selection-color: #1e3a8a;
            }
            QHeaderView::section {
                background-color: #f8fafc;
                color: #475569;
                font-weight: 700;
                padding: 6px;
                border: 1px solid #e2e8f0;
            }
            QPushButton {
                background-color: #e2e8f0;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #cbd5e1;
                border-color: #94a3b8;
            }
            QPushButton:disabled {
                background-color: #f1f5f9;
                color: #94a3b8;
                border-color: #e2e8f0;
            }
            #btnTheme {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                color: #0f172a;
                padding: 5px 12px;
                font-size: 12px;
            }
            #btnTheme:hover {
                background-color: #f1f5f9;
                border-color: #3b82f6;
            }
            #btnStart {
                background-color: #10b981;
                color: #ffffff;
                border: 1px solid #059669;
            }
            #btnStart:hover {
                background-color: #059669;
            }
            #btnStop {
                background-color: #ef4444;
                color: #ffffff;
                border: 1px solid #dc2626;
            }
            #btnStop:hover {
                background-color: #dc2626;
            }
            #btnBrowser {
                background-color: #3b82f6;
                color: #ffffff;
                border: 1px solid #2563eb;
            }
            #btnBrowser:hover {
                background-color: #2563eb;
            }
            #logView {
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                line-height: 1.4;
            }
            QStatusBar {
                color: #64748b;
                font-size: 11.5px;
            }
            #urlLabel {
                font-weight: 600;
                color: #0284c7;
            }
        """)


def main():
    qInstallMessageHandler(qt_suppress_handler)
    app = QApplication(sys.argv)
    app.setApplicationName("SchoolNet Server Launcher")
    
    window = SchoolNetLauncher()
    window.show()
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
