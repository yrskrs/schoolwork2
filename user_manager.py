#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Графічний модуль реєстрації та керування користувачами SchoolNet на PyQt6.
Призначений для швидкої реєстрації вчителів, адміністраторів та користувачів,
а також перегляду та адміністрування облікових записів.
Працює як у Linux, так і у Windows.
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# Налаштування шляхів та оточення Django
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "schoolnet.settings")
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu-sandbox --disable-logging"
os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.webenginecontext.debug=false;qt.qpa.*=false;qt.qpa.wayland*=false;qt.qpa.wayland.textinput*=false"
os.environ["PYTHONWARNINGS"] = "ignore"

try:
    import django
    django.setup()
    from django.contrib.auth.models import User
    from feed.models import Teacher, Subject, ClassGroup
    from django.db import transaction
    DJANGO_AVAILABLE = True
except Exception as e:
    DJANGO_AVAILABLE = False
    DJANGO_ERROR = str(e)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QGroupBox,
    QFrame, QMessageBox, QStatusBar, QFileDialog, QSplitter,
    QListWidget, QListWidgetItem, QColorDialog, QInputDialog,
    QScrollArea
)
from PyQt6.QtCore import Qt, QSettings, qInstallMessageHandler
from PyQt6.QtGui import QColor, QFont, QIcon, QAction, QGuiApplication


def qt_suppress_handler(mode, context, message):
    """Приглушує некритичні системні попередження Wayland та Sandbox."""
    msg_low = message.lower()
    if any(k in msg_low for k in ("wayland", "sandbox", "zwp_text_input", "leave event", "user namespace")):
        return
    # Критичні помилки виводимо
    if mode in (3, 4):  # QtFatalMsg, QtCriticalMsg
        sys.stderr.write(f"{message}\n")


COLOR_PALETTE = [
    ("#6366f1", "Індиго"),
    ("#3b82f6", "Синій"),
    ("#06b6d4", "Блакитний"),
    ("#10b981", "Смарагдовий"),
    ("#84cc16", "Лаймовий"),
    ("#f59e0b", "Бурштиновий"),
    ("#f97316", "Помаранчевий"),
    ("#ef4444", "Червоний"),
    ("#ec4899", "Рожевий"),
    ("#8b5cf6", "Фіолетовий"),
]


class UserManagerWindow(QMainWindow):
    """Головне вікно реєстрації та керування користувачами SchoolNet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("👥 Реєстрація та Керування Користувачами — SchoolNet")
        self.resize(1120, 720)
        self.setMinimumSize(850, 560)

        self.settings = QSettings("SchoolNet", "UserManager")
        self.current_theme = self.settings.value("theme", "dark")
        self.selected_avatar_color = "#6366f1"

        if not DJANGO_AVAILABLE:
            QMessageBox.critical(
                self,
                "Помилка бази даних",
                f"Не вдалося ініціалізувати Django/Базу даних:\n{DJANGO_ERROR}\n\n"
                "Перевірте наявність файлу schoolnet.sqlite3 та коректність venv."
            )

        self.init_ui()
        self.apply_theme()
        self.load_subjects_and_classes()
        self.refresh_users_table()

    def init_ui(self):
        """Ініціалізація компонентів інтерфейсу."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(14)

        # ── 1. Верхня шапка (Header) ──────────────────────────────────────────
        header_card = QFrame()
        header_card.setObjectName("headerCard")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)

        # Заголовок та підзаголовок
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        app_title = QLabel("👥 Реєстрація та Керування Користувачами")
        app_title.setObjectName("appTitle")
        app_sub = QLabel("Створення нових облікових записів (вчителів / адмінів) та перегляд зареєстрованих")
        app_sub.setObjectName("appSubtitle")
        title_box.addWidget(app_title)
        title_box.addWidget(app_sub)
        header_layout.addLayout(title_box, 1)

        # Статистика користувачів (Badge)
        self.stats_badge = QLabel("Всього: 0 | Вчителів: 0 | Адмінів: 0")
        self.stats_badge.setObjectName("statsBadge")
        header_layout.addWidget(self.stats_badge)

        # Перемикач теми
        self.btn_theme = QPushButton("🌙 Темна")
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.setToolTip("Перемкнути між світлою та темною темою")
        self.btn_theme.clicked.connect(self.toggle_theme)
        header_layout.addWidget(self.btn_theme)

        main_layout.addWidget(header_card)

        # ── 2. Головний спліттер (Ліворуч: Форма, Праворуч: Таблиця) ─────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ── ЛІВА ПАНЕЛЬ: Форма реєстрації ────────────────────────────────────
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        reg_group = QGroupBox("📝 Реєстрація нового користувача")
        reg_group_layout = QVBoxLayout(reg_group)
        reg_group_layout.setContentsMargins(14, 16, 14, 14)
        reg_group_layout.setSpacing(10)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        form_container = QWidget()
        form_layout = QVBoxLayout(form_container)
        form_layout.setContentsMargins(4, 4, 4, 4)
        form_layout.setSpacing(10)

        # Поле: Логін
        form_layout.addWidget(QLabel("🔑 Логін (Username) *:"))
        self.input_username = QLineEdit()
        self.input_username.setPlaceholderText("наприклад: ivanov_math")
        self.input_username.textChanged.connect(self.on_username_changed)
        form_layout.addWidget(self.input_username)
        self.lbl_username_hint = QLabel("")
        self.lbl_username_hint.setStyleSheet("font-size: 11px; color: #94a3b8;")
        form_layout.addWidget(self.lbl_username_hint)

        # Поле: ПІБ
        form_layout.addWidget(QLabel("👤 Повне ім'я (ПІБ) *:"))
        self.input_fullname = QLineEdit()
        self.input_fullname.setPlaceholderText("наприклад: Іванов Іван Іванович")
        form_layout.addWidget(self.input_fullname)

        # Поле: Пароль
        pass_label_layout = QHBoxLayout()
        pass_label_layout.addWidget(QLabel("🔒 Пароль *:"))
        pass_label_layout.addStretch()
        self.btn_toggle_pass = QPushButton("👁️ Показати")
        self.btn_toggle_pass.setFixedHeight(22)
        self.btn_toggle_pass.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_pass.setStyleSheet("font-size: 11px; padding: 1px 6px;")
        self.btn_toggle_pass.clicked.connect(self.toggle_password_visibility)
        pass_label_layout.addWidget(self.btn_toggle_pass)
        form_layout.addLayout(pass_label_layout)

        self.input_password = QLineEdit()
        self.input_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_password.setPlaceholderText("Мінімум 4 символи")
        form_layout.addWidget(self.input_password)

        # Поле: Підтвердження пароля
        form_layout.addWidget(QLabel("🔒 Підтвердження пароля *:"))
        self.input_password_confirm = QLineEdit()
        self.input_password_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_password_confirm.setPlaceholderText("Повторіть пароль")
        form_layout.addWidget(self.input_password_confirm)

        # Поле: Роль
        form_layout.addWidget(QLabel("👑 Роль у системі:"))
        self.combo_role = QComboBox()
        self.combo_role.addItem("👨‍🏫 Вчитель (Teacher)", "teacher")
        self.combo_role.addItem("👑 Супер-Адміністратор (Superuser)", "superuser")
        self.combo_role.addItem("👤 Звичайний користувач (Staff)", "staff")
        self.combo_role.currentIndexChanged.connect(self.on_role_changed)
        form_layout.addWidget(self.combo_role)

        # Поле: Колір аватара
        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("🎨 Колір аватара:"))
        self.avatar_color_preview = QLabel("   ")
        self.avatar_color_preview.setFixedSize(24, 24)
        self.avatar_color_preview.setStyleSheet(f"background-color: {self.selected_avatar_color}; border-radius: 12px; border: 1px solid #ffffff;")
        color_layout.addWidget(self.avatar_color_preview)
        
        self.btn_pick_color = QPushButton("Обрати колір...")
        self.btn_pick_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pick_color.clicked.connect(self.pick_avatar_color)
        color_layout.addWidget(self.btn_pick_color)
        color_layout.addStretch()
        form_layout.addLayout(color_layout)

        # Поле: Предмети (Checkbox list)
        self.lbl_subjects = QLabel("📚 Предмети викладання:")
        form_layout.addWidget(self.lbl_subjects)
        self.list_subjects = QListWidget()
        self.list_subjects.setFixedHeight(110)
        self.list_subjects.setToolTip("Оберіть предмети, які закріплені за вчителем")
        form_layout.addWidget(self.list_subjects)

        # Поле: Класи (Checkbox list)
        self.lbl_classes = QLabel("🏫 Класи:")
        form_layout.addWidget(self.lbl_classes)
        self.list_classes = QListWidget()
        self.list_classes.setFixedHeight(95)
        self.list_classes.setToolTip("Оберіть класи, за якими закріплений вчитель")
        form_layout.addWidget(self.list_classes)

        scroll_area.setWidget(form_container)
        reg_group_layout.addWidget(scroll_area)

        # Кнопки форми
        btn_form_layout = QHBoxLayout()
        self.btn_register = QPushButton("✨ Зареєструвати")
        self.btn_register.setObjectName("btnRegister")
        self.btn_register.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_register.clicked.connect(self.register_user)
        
        self.btn_clear_form = QPushButton("🧹 Очистити")
        self.btn_clear_form.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_form.clicked.connect(self.clear_form)

        btn_form_layout.addWidget(self.btn_register, 2)
        btn_form_layout.addWidget(self.btn_clear_form, 1)
        reg_group_layout.addLayout(btn_form_layout)

        left_layout.addWidget(reg_group)
        splitter.addWidget(left_widget)

        # ── ПРАВА ПАНЕЛЬ: Список користувачів ────────────────────────────────
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        list_group = QGroupBox("📋 Зареєстровані користувачі")
        list_group_layout = QVBoxLayout(list_group)
        list_group_layout.setContentsMargins(14, 16, 14, 14)
        list_group_layout.setSpacing(10)

        # Панель пошуку та фільтрації
        search_layout = QHBoxLayout()
        search_icon = QLabel("🔍")
        search_layout.addWidget(search_icon)
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Швидкий пошук за логіном, ПІБ, роллю чи предметом...")
        self.input_search.textChanged.connect(self.filter_users_table)
        search_layout.addWidget(self.input_search, 1)

        self.btn_refresh = QPushButton("🔄 Оновити")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.refresh_users_table)
        search_layout.addWidget(self.btn_refresh)
        list_group_layout.addLayout(search_layout)

        # Таблиця користувачів
        self.table_users = QTableWidget()
        self.table_users.setColumnCount(8)
        self.table_users.setHorizontalHeaderLabels([
            "ID", "Логін", "ПІБ", "Роль", "Предмети", "Класи", "Дата реєстрації", "Останній вхід"
        ])
        self.table_users.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_users.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_users.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_users.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_users.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table_users.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table_users.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table_users.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self.table_users.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_users.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_users.setAlternatingRowColors(True)
        self.table_users.doubleClicked.connect(self.on_table_double_click)
        list_group_layout.addWidget(self.table_users)

        # Нижня панель дій над вибраним користувачем
        actions_layout = QHBoxLayout()
        self.btn_change_pass = QPushButton("🔑 Змінити пароль")
        self.btn_change_pass.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_change_pass.clicked.connect(self.change_user_password)
        actions_layout.addWidget(self.btn_change_pass)

        self.btn_copy_info = QPushButton("📋 Скопіювати логін")
        self.btn_copy_info.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_info.clicked.connect(self.copy_selected_user_info)
        actions_layout.addWidget(self.btn_copy_info)

        self.btn_export = QPushButton("📤 Експорт у файл...")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.clicked.connect(self.export_users)
        actions_layout.addWidget(self.btn_export)

        actions_layout.addStretch()

        self.btn_delete_user = QPushButton("🗑️ Видалити")
        self.btn_delete_user.setObjectName("btnDelete")
        self.btn_delete_user.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete_user.clicked.connect(self.delete_selected_user)
        actions_layout.addWidget(self.btn_delete_user)

        list_group_layout.addLayout(actions_layout)
        right_layout.addWidget(list_group)
        splitter.addWidget(right_widget)

        # Співвідношення ширини панелей (38% ліва, 62% права)
        splitter.setSizes([380, 680])
        main_layout.addWidget(splitter, 1)

        # ── 3. Рядок стану (Status Bar) ──────────────────────────────────────
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Готово до реєстрації нових користувачів.")

    # ── Завантаження предметів та класів ─────────────────────────────────────
    def load_subjects_and_classes(self):
        """Завантажує доступні предмети та класи з БД у списки з чекбоксами."""
        if not DJANGO_AVAILABLE:
            return

        self.list_subjects.clear()
        try:
            for s in Subject.objects.all().order_by("name"):
                item = QListWidgetItem(f"{s.icon or '📚'} {s.name}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, s.id)
                self.list_subjects.addItem(item)
        except Exception:
            pass

        self.list_classes.clear()
        try:
            for c in ClassGroup.objects.all().order_by("grade", "letter"):
                item = QListWidgetItem(f"🏫 {c.name}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, c.id)
                self.list_classes.addItem(item)
        except Exception:
            pass

    # ── Обробники подій форми ────────────────────────────────────────────────
    def on_username_changed(self, text):
        """Перевіряє унікальність логіна на льоту."""
        text = text.strip()
        if not text:
            self.lbl_username_hint.setText("")
            return

        if not DJANGO_AVAILABLE:
            return

        if User.objects.filter(username=text).exists():
            self.lbl_username_hint.setText("⚠️ Цей логін вже зайнятий!")
            self.lbl_username_hint.setStyleSheet("font-size: 11px; color: #ef4444; font-weight: 600;")
        else:
            self.lbl_username_hint.setText("✅ Логін вільний")
            self.lbl_username_hint.setStyleSheet("font-size: 11px; color: #10b981; font-weight: 600;")

    def on_role_changed(self, index):
        """Ховає або показує вибір предметів відповідно до обраної ролі."""
        role = self.combo_role.currentData()
        is_teacher_role = role in ("teacher", "superuser")
        self.lbl_subjects.setVisible(is_teacher_role)
        self.list_subjects.setVisible(is_teacher_role)
        self.lbl_classes.setVisible(is_teacher_role)
        self.list_classes.setVisible(is_teacher_role)

    def toggle_password_visibility(self):
        """Перемикає видимість паролів."""
        if self.input_password.echoMode() == QLineEdit.EchoMode.Password:
            self.input_password.setEchoMode(QLineEdit.EchoMode.Normal)
            self.input_password_confirm.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_pass.setText("🙈 Сховати")
        else:
            self.input_password.setEchoMode(QLineEdit.EchoMode.Password)
            self.input_password_confirm.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_pass.setText("👁️ Показати")

    def pick_avatar_color(self):
        """Вибір кольору аватара через QColorDialog."""
        color = QColorDialog.getColor(QColor(self.selected_avatar_color), self, "Оберіть колір аватара")
        if color.isValid():
            self.selected_avatar_color = color.name()
            self.avatar_color_preview.setStyleSheet(
                f"background-color: {self.selected_avatar_color}; border-radius: 12px; border: 1px solid #ffffff;"
            )

    def clear_form(self):
        """Очищує поля форми реєстрації."""
        self.input_username.clear()
        self.input_fullname.clear()
        self.input_password.clear()
        self.input_password_confirm.clear()
        self.combo_role.setCurrentIndex(0)
        self.selected_avatar_color = "#6366f1"
        self.avatar_color_preview.setStyleSheet(
            f"background-color: {self.selected_avatar_color}; border-radius: 12px; border: 1px solid #ffffff;"
        )
        self.lbl_username_hint.setText("")

        for i in range(self.list_subjects.count()):
            self.list_subjects.item(i).setCheckState(Qt.CheckState.Unchecked)

        for i in range(self.list_classes.count()):
            self.list_classes.item(i).setCheckState(Qt.CheckState.Unchecked)

        self.input_username.setFocus()
        self.status_bar.showMessage("Форму очищено.")

    # ── Реєстрація нового користувача ────────────────────────────────────────
    def register_user(self):
        """Валідує дані та створює нового користувача в Django ORM."""
        if not DJANGO_AVAILABLE:
            QMessageBox.critical(self, "Помилка", "База даних недоступна.")
            return

        username = self.input_username.text().strip()
        full_name = self.input_fullname.text().strip()
        password = self.input_password.text()
        password_confirm = self.input_password_confirm.text()
        role = self.combo_role.currentData()

        # Валідація
        if not username:
            QMessageBox.warning(self, "Помилка валідації", "Будь ласка, введіть логін (Username).")
            self.input_username.setFocus()
            return

        if User.objects.filter(username=username).exists():
            QMessageBox.warning(self, "Помилка валідації", f"Користувач з логіном «{username}» вже існує!")
            self.input_username.setFocus()
            return

        if not full_name:
            QMessageBox.warning(self, "Помилка валідації", "Будь ласка, введіть повне ім'я (ПІБ).")
            self.input_fullname.setFocus()
            return

        if not password:
            QMessageBox.warning(self, "Помилка валідації", "Будь ласка, введіть пароль.")
            self.input_password.setFocus()
            return

        if len(password) < 4:
            QMessageBox.warning(self, "Помилка валідації", "Пароль повинен містити щонайменше 4 символи.")
            self.input_password.setFocus()
            return

        if password != password_confirm:
            QMessageBox.warning(self, "Помилка валідації", "Паролі не співпадають. Перевірте правильність введення.")
            self.input_password_confirm.setFocus()
            return

        # Збір вибраних предметів і класів
        selected_subject_ids = []
        for i in range(self.list_subjects.count()):
            item = self.list_subjects.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_subject_ids.append(item.data(Qt.ItemDataRole.UserRole))

        selected_class_ids = []
        for i in range(self.list_classes.count()):
            item = self.list_classes.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_class_ids.append(item.data(Qt.ItemDataRole.UserRole))

        try:
            with transaction.atomic():
                is_superuser = (role == "superuser")
                is_staff = (role in ("superuser", "teacher", "staff"))

                user = User.objects.create_user(
                    username=username,
                    password=password,
                    is_superuser=is_superuser,
                    is_staff=is_staff
                )

                # Створюємо профіль Teacher
                teacher = Teacher.objects.create(
                    user=user,
                    full_name=full_name,
                    avatar_color=self.selected_avatar_color
                )

                if selected_subject_ids:
                    teacher.subjects.set(Subject.objects.filter(id__in=selected_subject_ids))

                if selected_class_ids:
                    teacher.classes.set(ClassGroup.objects.filter(id__in=selected_class_ids))

            QMessageBox.information(
                self,
                "Успішна реєстрація",
                f"🎉 Користувача «{full_name}» ({username}) успішно зареєстровано в системі SchoolNet!\n\n"
                f"Логін: {username}\n"
                f"Роль: {self.combo_role.currentText()}"
            )

            self.clear_form()
            self.refresh_users_table()
            self.status_bar.showMessage(f"✅ Користувача '{username}' успішно зареєстровано!")

        except Exception as e:
            QMessageBox.critical(self, "Помилка створення", f"Не вдалося зареєструвати користувача:\n{str(e)}")

    # ── Завантаження та відображення таблиці користувачів ────────────────────
    def refresh_users_table(self):
        """Оновлює таблицю зареєстрованих користувачів із бази даних."""
        if not DJANGO_AVAILABLE:
            return

        self.table_users.setRowCount(0)
        users = User.objects.all().select_related("teacher_profile").prefetch_related(
            "teacher_profile__subjects", "teacher_profile__classes"
        ).order_by("-id")

        total_users = users.count()
        total_teachers = 0
        total_admins = 0

        for user in users:
            row_idx = self.table_users.rowCount()
            self.table_users.insertRow(row_idx)

            # Отримуємо дані
            user_id = str(user.id)
            username = user.username
            is_super = user.is_superuser
            if is_super:
                total_admins += 1

            teacher_profile = getattr(user, "teacher_profile", None)
            if teacher_profile:
                total_teachers += 1
                full_name = teacher_profile.full_name
                subjects = ", ".join([s.name for s in teacher_profile.subjects.all()]) or "—"
                classes = ", ".join([c.name for c in teacher_profile.classes.all()]) or "—"
            else:
                full_name = f"{user.first_name} {user.last_name}".strip() or "—"
                subjects = "—"
                classes = "—"

            if is_super:
                role_text = "👑 Супер-Адмін"
            elif teacher_profile:
                role_text = "👨‍🏫 Вчитель"
            else:
                role_text = "👤 Користувач"

            date_joined = user.date_joined.strftime("%Y-%m-%d %H:%M") if user.date_joined else "—"
            last_login = user.last_login.strftime("%Y-%m-%d %H:%M") if user.last_login else "Ніколи"

            # Вставка комірок
            item_id = QTableWidgetItem(user_id)
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_id.setData(Qt.ItemDataRole.UserRole, user.id)

            item_username = QTableWidgetItem(username)
            item_username.setFont(QFont("", -1, QFont.Weight.Bold))

            item_fullname = QTableWidgetItem(full_name)
            item_role = QTableWidgetItem(role_text)
            item_role.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_subjects = QTableWidgetItem(subjects)
            item_classes = QTableWidgetItem(classes)

            item_date = QTableWidgetItem(date_joined)
            item_date.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_login = QTableWidgetItem(last_login)
            item_login.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.table_users.setItem(row_idx, 0, item_id)
            self.table_users.setItem(row_idx, 1, item_username)
            self.table_users.setItem(row_idx, 2, item_fullname)
            self.table_users.setItem(row_idx, 3, item_role)
            self.table_users.setItem(row_idx, 4, item_subjects)
            self.table_users.setItem(row_idx, 5, item_classes)
            self.table_users.setItem(row_idx, 6, item_date)
            self.table_users.setItem(row_idx, 7, item_login)

        # Оновлення бейджа статистики
        self.stats_badge.setText(f"Всього: {total_users} | Вчителів: {total_teachers} | Адмінів: {total_admins}")
        self.filter_users_table(self.input_search.text())
        self.status_bar.showMessage(f"Оновлено. Завантажено {total_users} користувачів.")

    def filter_users_table(self, query):
        """Фільтрація таблиці за введеним текстом."""
        query = query.strip().lower()
        for row in range(self.table_users.rowCount()):
            if not query:
                self.table_users.setRowHidden(row, False)
                continue

            match = False
            for col in range(self.table_users.columnCount()):
                item = self.table_users.item(row, col)
                if item and query in item.text().lower():
                    match = True
                    break
            self.table_users.setRowHidden(row, not match)

    def get_selected_user_id(self):
        """Повертає ID вибраного в таблиці користувача."""
        current_row = self.table_users.currentRow()
        if current_row < 0:
            return None
        item = self.table_users.item(current_row, 0)
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    # ── Дії над користувачем ────────────────────────────────────────────────
    def on_table_double_click(self, index):
        """Подвійний клік копіює логін або відкриває зміну пароля."""
        self.change_user_password()

    def change_user_password(self):
        """Зміна пароля для обраного користувача."""
        user_id = self.get_selected_user_id()
        if not user_id:
            QMessageBox.information(self, "Вибір користувача", "Будь ласка, оберіть користувача в таблиці.")
            return

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            QMessageBox.critical(self, "Помилка", "Користувача не знайдено.")
            return

        new_password, ok = QInputDialog.getText(
            self,
            "Зміна пароля",
            f"Введіть новий пароль для користувача «{user.username}»:",
            QLineEdit.EchoMode.Password
        )

        if ok and new_password:
            if len(new_password.strip()) < 4:
                QMessageBox.warning(self, "Помилка", "Пароль повинен бути не менше 4 символів.")
                return

            user.set_password(new_password.strip())
            user.save()
            QMessageBox.information(
                self,
                "Пароль змінено",
                f"✅ Пароль для користувача «{user.username}» успішно оновлено!"
            )
            self.status_bar.showMessage(f"Пароль для '{user.username}' змінено.")

    def copy_selected_user_info(self):
        """Копіює облікові дані обраного користувача в буфер обміну."""
        current_row = self.table_users.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "Вибір користувача", "Будь ласка, оберіть користувача в таблиці.")
            return

        username = self.table_users.item(current_row, 1).text()
        fullname = self.table_users.item(current_row, 2).text()
        role = self.table_users.item(current_row, 3).text()

        text = f"Користувач SchoolNet:\nЛогін: {username}\nПІБ: {fullname}\nРоль: {role}"
        QGuiApplication.clipboard().setText(text)
        self.status_bar.showMessage(f"📋 Дані користувача '{username}' скопійовано в буфер обміну!")

    def delete_selected_user(self):
        """Видалення користувача з підтвердженням."""
        user_id = self.get_selected_user_id()
        if not user_id:
            QMessageBox.information(self, "Вибір користувача", "Будь ласка, оберіть користувача для видалення.")
            return

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            QMessageBox.critical(self, "Помилка", "Користувача не знайдено.")
            return

        # Захист від видалення єдиного суперюзера або активного адміна
        if User.objects.filter(is_superuser=True).count() <= 1 and user.is_superuser:
            reply = QMessageBox.warning(
                self,
                "Увага! Останній супер-адміністратор",
                f"Користувач «{user.username}» є єдиним супер-адміністратором системи!\n"
                "Якщо ви видалите його, доступ до налаштувань адміна буде втрачено.\n\n"
                "Ви дійсно впевнені, що хочете видалити?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        else:
            reply = QMessageBox.question(
                self,
                "Підтвердження видалення",
                f"Ви дійсно бажаєте видалити користувача «{user.username}»?\n"
                "Цю дію неможливо скасувати.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            uname = user.username
            user.delete()
            self.refresh_users_table()
            self.status_bar.showMessage(f"🗑️ Користувача '{uname}' успішно видалено.")
            QMessageBox.information(self, "Успішно", f"Користувача «{uname}» видалено з системи.")
        except Exception as e:
            QMessageBox.critical(self, "Помилка видалення", f"Не вдалося видалити користувача:\n{str(e)}")

    def export_users(self):
        """Експорт списку зареєстрованих користувачів у файл (CSV або TXT)."""
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Експорт користувачів",
            str(BASE_DIR / f"schoolnet_users_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"),
            "CSV файли (*.csv);;Текстові файли (*.txt);;Усі файли (*.*)"
        )

        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8-sig") as f:
                if file_path.endswith(".csv"):
                    f.write("ID;Логін;ПІБ;Роль;Предмети;Класи;Дата реєстрації;Останній вхід\n")
                    for row in range(self.table_users.rowCount()):
                        row_data = [self.table_users.item(row, col).text() if self.table_users.item(row, col) else "" for col in range(8)]
                        f.write(";".join([f'"{d}"' for d in row_data]) + "\n")
                else:
                    f.write(f"=== Звіт зареєстрованих користувачів SchoolNet ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===\n\n")
                    for row in range(self.table_users.rowCount()):
                        u_id = self.table_users.item(row, 0).text()
                        username = self.table_users.item(row, 1).text()
                        fullname = self.table_users.item(row, 2).text()
                        role = self.table_users.item(row, 3).text()
                        subj = self.table_users.item(row, 4).text()
                        classes = self.table_users.item(row, 5).text()
                        f.write(f"[{u_id}] {username} | {fullname} | {role}\n  Предмети: {subj} | Класи: {classes}\n\n")

            QMessageBox.information(self, "Експорт завершено", f"Файл успішно збережено:\n{file_path}")
            self.status_bar.showMessage(f"Експорт збережено у {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Помилка експорту", f"Не вдалося зберегти файл:\n{str(e)}")

    # ── Теми оформлення (Світла / Темна) ────────────────────────────────────
    def toggle_theme(self):
        """Перемикає тему та зберігає налаштування."""
        if self.current_theme == "dark":
            self.current_theme = "light"
            self.btn_theme.setText("☀️ Світла")
        else:
            self.current_theme = "dark"
            self.btn_theme.setText("🌙 Темна")

        self.settings.setValue("theme", self.current_theme)
        self.apply_theme()

    def apply_theme(self):
        """Застосовує поточну тему оформлення."""
        if self.current_theme == "light":
            self.btn_theme.setText("☀️ Світла")
            self.apply_light_theme()
        else:
            self.btn_theme.setText("🌙 Темна")
            self.apply_dark_theme()

    def apply_dark_theme(self):
        """Сучасна темна тема у стилі SchoolNet."""
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
            #statsBadge {
                background: rgba(99, 102, 241, 0.18);
                color: #a5b4fc;
                border: 1px solid #6366f1;
                border-radius: 8px;
                padding: 5px 12px;
                font-weight: 700;
                font-size: 12px;
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
            QLineEdit, QComboBox, QListWidget, QTableWidget {
                background-color: #1e293b;
                border: 1px solid #475569;
                border-radius: 7px;
                padding: 6px 10px;
                color: #ffffff;
                selection-background-color: #6366f1;
            }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus, QTableWidget:focus {
                border-color: #818cf8;
                outline: none;
            }
            QListWidget::item {
                padding: 4px 6px;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background-color: #334155;
            }
            QListWidget::item:selected {
                background-color: #4338ca;
                color: #ffffff;
            }
            QTableWidget {
                gridline-color: #334155;
                alternate-background-color: #162032;
                border: 1px solid #334155;
                border-radius: 8px;
            }
            QHeaderView::section {
                background-color: #1e293b;
                color: #94a3b8;
                font-weight: 700;
                border: none;
                border-bottom: 2px solid #334155;
                padding: 6px 8px;
            }
            QPushButton {
                background-color: #334155;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 7px;
                padding: 7px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #475569;
                border-color: #64748b;
            }
            QPushButton:pressed {
                background-color: #1e293b;
            }
            #btnRegister {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #8b5cf6);
                color: #ffffff;
                border: none;
                font-weight: 700;
                padding: 9px 16px;
                border-radius: 8px;
            }
            #btnRegister:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f46e5, stop:1 #7c3aed);
            }
            #btnDelete {
                background-color: rgba(239, 68, 68, 0.15);
                color: #f87171;
                border: 1px solid #ef4444;
            }
            #btnDelete:hover {
                background-color: #ef4444;
                color: #ffffff;
            }
            QStatusBar {
                background-color: #0b1120;
                color: #94a3b8;
                border-top: 1px solid #1e293b;
            }
            QScrollBar:vertical {
                background: #0f172a;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #334155;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: #475569;
            }
        """)

    def apply_light_theme(self):
        """Світла тема з акуратними контрастами."""
        self.setStyleSheet("""
            QMainWindow, QWidget, QDialog {
                background-color: #f8fafc;
                color: #0f172a;
                font-family: 'Segoe UI', system-ui, -apple-system, Roboto, sans-serif;
                font-size: 13px;
            }
            #headerCard {
                background: #ffffff;
                border: 1px solid #e2e8f0;
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
            #statsBadge {
                background: #eff6ff;
                color: #2563eb;
                border: 1px solid #bfdbfe;
                border-radius: 8px;
                padding: 5px 12px;
                font-weight: 700;
                font-size: 12px;
            }
            QGroupBox {
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                margin-top: 8px;
                padding-top: 16px;
                font-weight: 700;
                color: #334155;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QLineEdit, QComboBox, QListWidget, QTableWidget {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                padding: 6px 10px;
                color: #0f172a;
                selection-background-color: #6366f1;
            }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus, QTableWidget:focus {
                border-color: #6366f1;
            }
            QListWidget::item {
                padding: 4px 6px;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background-color: #f1f5f9;
            }
            QListWidget::item:selected {
                background-color: #e0e7ff;
                color: #3730a3;
            }
            QTableWidget {
                gridline-color: #e2e8f0;
                alternate-background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }
            QHeaderView::section {
                background-color: #f1f5f9;
                color: #475569;
                font-weight: 700;
                border: none;
                border-bottom: 2px solid #cbd5e1;
                padding: 6px 8px;
            }
            QPushButton {
                background-color: #f1f5f9;
                color: #1e293b;
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                padding: 7px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
                border-color: #94a3b8;
            }
            #btnRegister {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f46e5, stop:1 #7c3aed);
                color: #ffffff;
                border: none;
                font-weight: 700;
                padding: 9px 16px;
                border-radius: 8px;
            }
            #btnRegister:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4338ca, stop:1 #6d28d9);
            }
            #btnDelete {
                background-color: #fef2f2;
                color: #dc2626;
                border: 1px solid #fecaca;
            }
            #btnDelete:hover {
                background-color: #dc2626;
                color: #ffffff;
            }
            QStatusBar {
                background-color: #ffffff;
                color: #64748b;
                border-top: 1px solid #e2e8f0;
            }
        """)


def main():
    """Точка входу для автономного запуску."""
    qInstallMessageHandler(qt_suppress_handler)
    app = QApplication(sys.argv)
    app.setApplicationName("SchoolNet-UserManager")
    app.setOrganizationName("SchoolNet")

    window = UserManagerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
