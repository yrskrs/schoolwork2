"""
Модуль для повного синтаксичного розбору, декомпіляції та візуалізації проєктів Scratch 3 (.sb3).
Розбирає повне дерево абстрактного синтаксису (AST) Scratch 3, включаючи:
- Вкладені цикли та умови (SUBSTACK, SUBSTACK2)
- Складні математичні та логічні вирази (Operators, Sensing, Variables)
- Власні блоки та процедури (Custom Blocks / Procedures)
- Всі розширення Scratch 3 (Pen, Music, Microbit, Video, Translate тощо)
- Інтерактивне відтворення через TurboWarp Embed
"""

import os
import io
import json
import zipfile
import html
import urllib.parse


OPCODE_TRANSLATIONS = {
    # ── Події (Events) ──
    'event_whenflagclicked': '🚩 Коли натиснуто 🟢 (зелений прапорець)',
    'event_whenkeypressed': '⌨️ Коли клавішу [{key}] натиснуто',
    'event_whenthisspriteclicked': '🖱️ Коли цей спрайт натиснуто',
    'event_whenstageclicked': '🖱️ Коли сцену натиснуто',
    'event_whenbackdropswitchesto': '🖼️ Коли тло зміниться на [{backdrop}]',
    'event_whengreaterthan': '📈 Коли {menu} > {value}',
    'event_whenbroadcastreceived': '📨 Коли я отримую повідомлення [{broadcast}]',
    'event_broadcast': '📢 Оповістити [{broadcast}]',
    'event_broadcastandwait': '📢 Оповістити [{broadcast}] і чекати',

    # ── Рух (Motion) ──
    'motion_movesteps': '👣 Перемістити на {steps} кроків',
    'motion_turnright': '↷ Повернути праворуч ↻ на {degrees}°',
    'motion_turnleft': '↶ Повернути ліворуч ↺ на {degrees}°',
    'motion_goto': '📍 Перейти до [{to}]',
    'motion_gotoxy': '📍 Перемістити в x: {x}, y: {y}',
    'motion_glideto': '✈️ Ковзати {secs} сек до [{to}]',
    'motion_glidesecstoxy': '✈️ Ковзати {secs} сек до x: {x}, y: {y}',
    'motion_pointindirection': '🧭 Повернути в напрямку {direction}°',
    'motion_pointtowards': '🧭 Спрямувати до [{towards}]',
    'motion_changexby': '↔️ Змінити x на {dx}',
    'motion_setx': '↔️ Встановити x в {x}',
    'motion_changeyby': '↕️ Змінити y на {dy}',
    'motion_sety': '↕️ Встановити y в {y}',
    'motion_ifonedgebounce': '⚡ Якщо на межі, відбити',
    'motion_setrotationstyle': '🔄 Встановити стиль обертання [{style}]',
    'motion_xposition': 'положення x',
    'motion_yposition': 'положення y',
    'motion_direction': 'напрямок',

    # ── Вигляд (Looks) ──
    'looks_sayforsecs': '💬 Говорити {message} протягом {secs} сек',
    'looks_say': '💬 Говорити {message}',
    'looks_thinkforsecs': '💭 Думати {message} протягом {secs} сек',
    'looks_think': '💭 Думати {message}',
    'looks_switchcostumeto': '🎭 Змінити костюм на [{costume}]',
    'looks_nextcostume': '🎭 Наступний костюм',
    'looks_switchbackdropto': '🖼️ Змінити тло на [{backdrop}]',
    'looks_nextbackdrop': '🖼️ Наступне тло',
    'looks_changesizeby': '🔍 Змінити розмір на {change}%',
    'looks_setsizeto': '🔍 Встановити розмір {size}%',
    'looks_changeeffectby': '✨ Змінити ефект [{effect}] на {change}',
    'looks_seteffectto': '✨ Встановити ефект [{effect}] в {value}',
    'looks_cleargraphiceffects': '✨ Очистити графічні ефекти',
    'looks_show': '👁️ Показати',
    'looks_hide': '🙈 Сховати',
    'looks_gotofrontback': '📑 Перейти на [{frontback}] план',
    'looks_goforwardbackwardlayers': '📑 Перемістити вперед/назад на {num} шарів',
    'looks_costumenumbername': 'номер/ім\'я костюма',
    'looks_backdropnumbername': 'номер/ім\'я тла',
    'looks_size': 'розмір',

    # ── Звук (Sound) ──
    'sound_playuntildone': '🔊 Відтворити звук [{sound}] до кінця',
    'sound_play': '🔊 Відтворити звук [{sound}]',
    'sound_stopallsounds': '🔇 Зупинити всі звуки',
    'sound_changeeffectby': '🎚️ Змінити звуковий ефект [{effect}] на {value}',
    'sound_seteffectto': '🎚️ Встановити звуковий ефект [{effect}] в {value}',
    'sound_cleareffects': '🎚️ Очистити звукові ефекти',
    'sound_changevolumeby': '🔈 Змінити гучність на {volume}%',
    'sound_setvolumeto': '🔈 Встановити гучність {volume}%',
    'sound_volume': 'гучність',

    # ── Керування (Control) ──
    'control_wait': '⏳ Чекати {duration} сек',
    'control_repeat': '🔁 Повторити {times} разів',
    'control_forever': '🔄 Завжди',
    'control_if': '❓ Якщо <{condition}>, то',
    'control_if_else': '❓ Якщо <{condition}>, то',
    'control_wait_until': '⏳ Чекати доки <{condition}>',
    'control_repeat_until': '🔁 Повторювати доки <{condition}>',
    'control_while': '🔁 Поки <{condition}>',
    'control_for_each': '🔁 Для кожного {variable} в {range}',
    'control_stop': '🛑 Зупинити [{stop_option}]',
    'control_start_as_clone': '👥 Коли я починаю як клон',
    'control_create_clone_of': '👥 Створити клон [{clone_option}]',
    'control_delete_this_clone': '❌ Вилучити цей клон',

    # ── Датчики (Sensing) ──
    'sensing_touchingobject': 'торкається [{touching}]?',
    'sensing_touchingcolor': 'торкається кольору [{color}]?',
    'sensing_coloristouchingcolor': 'колір [{color1}] торкається [{color2}]?',
    'sensing_distanceto': 'відстань до [{object}]',
    'sensing_askandwait': '❓ Запитати {question} і чекати',
    'sensing_answer': 'відповідь',
    'sensing_keypressed': 'клавішу [{key}] натиснуто?',
    'sensing_mousedown': 'мишку натиснуто?',
    'sensing_mousex': 'мишка x',
    'sensing_mousey': 'мишка y',
    'sensing_loudness': 'гучність таймера',
    'sensing_timer': 'таймер',
    'sensing_resettimer': '⏱️ Перезапустити таймер',
    'sensing_of': '{property} об\'єкта [{object}]',
    'sensing_current': 'поточний {menu}',
    'sensing_dayssince2000': 'днів від 2000 року',
    'sensing_username': 'ім\'я користувача',

    # ── Оператори (Operators) ──
    'operator_add': '({num1} + {num2})',
    'operator_subtract': '({num1} - {num2})',
    'operator_multiply': '({num1} * {num2})',
    'operator_divide': '({num1} / {num2})',
    'operator_random': 'випадкове від {from} до {to}',
    'operator_lt': '<{operand1} < {operand2}>',
    'operator_equals': '<{operand1} = {operand2}>',
    'operator_gt': '<{operand1} > {operand2}>',
    'operator_and': '<{operand1} І {operand2}>',
    'operator_or': '<{operand1} АБО {operand2}>',
    'operator_not': '<НЕ {operand}>',
    'operator_join': 'з\'єднати({string1}, {string2})',
    'operator_letter_of': 'символ {letter} у {string}',
    'operator_length': 'довжина({string})',
    'operator_contains': '<{string1} містить {string2}?>',
    'operator_mod': '({num1} mod {num2})',
    'operator_round': 'округлити({num})',
    'operator_mathop': '{operator}({num})',

    # ── Змінні та списки (Data) ──
    'data_setvariableto': '📊 Встановити [{variable}] = {value}',
    'data_changevariableby': '➕ Змінити [{variable}] на {value}',
    'data_showvariable': '👁️ Показати змінну [{variable}]',
    'data_hidevariable': '🙈 Сховати змінну [{variable}]',
    'data_addtolist': '📝 Додати {item} до списку [{list}]',
    'data_deleteoflist': '❌ Вилучити {index} з списку [{list}]',
    'data_deletealloflist': '❌ Вилучити все з списку [{list}]',
    'data_insertatlist': '📥 Вставити {item} у позицію {index} списку [{list}]',
    'data_replaceitemoflist': '✏️ Замінити елемент {index} у [{list}] на {item}',
    'data_itemoflist': 'елемент {index} списку [{list}]',
    'data_itemnumoflist': 'номер {item} у списку [{list}]',
    'data_lengthoflist': 'довжина списку [{list}]',
    'data_listcontainsitem': '<список [{list}] містить {item}?>',
    'data_showlist': '👁️ Показати список [{list}]',
    'data_hidelist': '🙈 Сховати список [{list}]',

    # ── Власні блоки (Procedures) ──
    'procedures_definition': '🧩 Визначення власного блоку:',
    'procedures_call': '📞 Виклик блоку:',

    # ── Розширення Олівець (Pen) ──
    'pen_clear': '🧹 Очистити все (олівець)',
    'pen_stamp': '🖨️ Штамп',
    'pen_penDown': '✏️ Опустити олівець',
    'pen_penUp': '✏️ Підняти олівець',
    'pen_setPenColorToColor': '🎨 Встановити колір олівця {color}',
    'pen_changePenSizeBy': '✏️ Змінити товщину олівця на {size}',
    'pen_setPenSizeTo': '✏️ Встановити товщину олівця {size}',

    # ── Розширення Музика (Music) ──
    'music_playDrumForBeats': '🥁 Зіграти на барабані {drum} ({beats} тактів)',
    'music_playNoteForBeats': '🎵 Зіграти ноту {note} ({beats} тактів)',
    'music_restForBeats': '🎵 Пауза ({beats} тактів)',
    'music_setTempo': '🎵 Встановити темп {tempo} уд/хв',
    'music_changeTempo': '🎵 Змінити темп на {tempo}',
}


def _get_block_category(opcode):
    """Повертає категорію блоку для колірного оформлення Scratch 3."""
    if opcode.startswith('event_'):
        return 'event', '#f59e0b', '#d97706', 'Події'
    elif opcode.startswith('motion_'):
        return 'motion', '#3b82f6', '#2563eb', 'Рух'
    elif opcode.startswith('looks_'):
        return 'looks', '#8b5cf6', '#7c3aed', 'Вигляд'
    elif opcode.startswith('sound_'):
        return 'sound', '#ec4899', '#db2777', 'Звук'
    elif opcode.startswith('control_'):
        return 'control', '#f97316', '#ea580c', 'Керування'
    elif opcode.startswith('sensing_'):
        return 'sensing', '#06b6d4', '#0891b2', 'Датчики'
    elif opcode.startswith('operator_'):
        return 'operator', '#10b981', '#059669', 'Оператори'
    elif opcode.startswith('data_'):
        return 'data', '#eab308', '#ca8a04', 'Змінні'
    elif opcode.startswith('procedures_'):
        return 'custom', '#a855f7', '#9333ea', 'Мої блоки'
    elif opcode.startswith('pen_'):
        return 'pen', '#0ea5e9', '#0284c7', 'Олівець'
    elif opcode.startswith('music_'):
        return 'music', '#06b6d4', '#0891b2', 'Музика'
    return 'other', '#64748b', '#475569', 'Блоки'


def _resolve_input_value(input_entry, blocks_dict):
    """
    Рекурсивно розбирає значення вхідного параметра Scratch 3 блоку:
    - Примітивне значення (число, рядок, колір, змінна)
    - Посилання на репортерний/математичний блок
    """
    if not input_entry or not isinstance(input_entry, (list, tuple)):
        return ""

    shadow_flag = input_entry[0] if len(input_entry) > 0 else 1
    val_data = input_entry[1] if len(input_entry) > 1 else None

    # Якщо це безпосередньо ID блоку-виразу
    if isinstance(val_data, str):
        if val_data in blocks_dict:
            return _format_reporter_block(blocks_dict[val_data], blocks_dict)
        return val_data

    # Якщо це примітивний масив [тип, значення, ...]
    if isinstance(val_data, list):
        if not val_data:
            return ""
        prim_type = val_data[0]
        prim_val = val_data[1] if len(val_data) > 1 else ""

        # Тип 12 = змінна: [12, "var_name", "var_id"]
        if prim_type == 12:
            return f"({prim_val})"
        # Тип 13 = список: [13, "list_name", "list_id"]
        elif prim_type == 13:
            return f"(список: {prim_val})"
        # Тип 10 = рядок: [10, "hello"]
        elif prim_type == 10:
            return f'"{prim_val}"'
        # Числа, кути, кольори
        return str(prim_val)

    # Якщо це shadow/obscured input з 3 елементів: [3, "block_id", [primitive]]
    if len(input_entry) >= 3 and isinstance(input_entry[1], str) and input_entry[1] in blocks_dict:
        return _format_reporter_block(blocks_dict[input_entry[1]], blocks_dict)

    return str(val_data) if val_data is not None else ""


def _format_reporter_block(block, blocks_dict):
    """Форматує репортерний (круглий або шестикутний) блок у вигляді зрозумілого виразу."""
    if not isinstance(block, dict):
        return str(block)

    opcode = block.get('opcode', '')
    inputs = block.get('inputs', {})
    fields = block.get('fields', {})

    # Обробка змінних і списків
    if opcode == 'data_variable':
        v_name = fields.get('VARIABLE', [''])[0] if fields.get('VARIABLE') else 'змінна'
        return f"({v_name})"
    elif opcode == 'data_listcontents':
        l_name = fields.get('LIST', [''])[0] if fields.get('LIST') else 'список'
        return f"(список {l_name})"

    # Оператори
    if opcode == 'operator_add':
        n1 = _resolve_input_value(inputs.get('NUM1'), blocks_dict) or '0'
        n2 = _resolve_input_value(inputs.get('NUM2'), blocks_dict) or '0'
        return f"({n1} + {n2})"
    elif opcode == 'operator_subtract':
        n1 = _resolve_input_value(inputs.get('NUM1'), blocks_dict) or '0'
        n2 = _resolve_input_value(inputs.get('NUM2'), blocks_dict) or '0'
        return f"({n1} - {n2})"
    elif opcode == 'operator_multiply':
        n1 = _resolve_input_value(inputs.get('NUM1'), blocks_dict) or '0'
        n2 = _resolve_input_value(inputs.get('NUM2'), blocks_dict) or '0'
        return f"({n1} * {n2})"
    elif opcode == 'operator_divide':
        n1 = _resolve_input_value(inputs.get('NUM1'), blocks_dict) or '0'
        n2 = _resolve_input_value(inputs.get('NUM2'), blocks_dict) or '1'
        return f"({n1} / {n2})"
    elif opcode == 'operator_random':
        f_val = _resolve_input_value(inputs.get('FROM'), blocks_dict) or '1'
        t_val = _resolve_input_value(inputs.get('TO'), blocks_dict) or '10'
        return f"випадкове({f_val}..{t_val})"
    elif opcode == 'operator_lt':
        op1 = _resolve_input_value(inputs.get('OPERAND1'), blocks_dict) or '0'
        op2 = _resolve_input_value(inputs.get('OPERAND2'), blocks_dict) or '0'
        return f"<{op1} < {op2}>"
    elif opcode == 'operator_equals':
        op1 = _resolve_input_value(inputs.get('OPERAND1'), blocks_dict) or '0'
        op2 = _resolve_input_value(inputs.get('OPERAND2'), blocks_dict) or '0'
        return f"<{op1} = {op2}>"
    elif opcode == 'operator_gt':
        op1 = _resolve_input_value(inputs.get('OPERAND1'), blocks_dict) or '0'
        op2 = _resolve_input_value(inputs.get('OPERAND2'), blocks_dict) or '0'
        return f"<{op1} > {op2}>"
    elif opcode == 'operator_and':
        op1 = _resolve_input_value(inputs.get('OPERAND1'), blocks_dict) or 'умова1'
        op2 = _resolve_input_value(inputs.get('OPERAND2'), blocks_dict) or 'умова2'
        return f"<{op1} І {op2}>"
    elif opcode == 'operator_or':
        op1 = _resolve_input_value(inputs.get('OPERAND1'), blocks_dict) or 'умова1'
        op2 = _resolve_input_value(inputs.get('OPERAND2'), blocks_dict) or 'умова2'
        return f"<{op1} АБО {op2}>"
    elif opcode == 'operator_not':
        op = _resolve_input_value(inputs.get('OPERAND'), blocks_dict) or 'умова'
        return f"<НЕ {op}>"
    elif opcode == 'operator_join':
        s1 = _resolve_input_value(inputs.get('STRING1'), blocks_dict) or '""'
        s2 = _resolve_input_value(inputs.get('STRING2'), blocks_dict) or '""'
        return f"з'єднати({s1}, {s2})"
    elif opcode == 'operator_letter_of':
        let = _resolve_input_value(inputs.get('LETTER'), blocks_dict) or '1'
        st = _resolve_input_value(inputs.get('STRING'), blocks_dict) or '""'
        return f"символ_{let}_у({st})"
    elif opcode == 'operator_length':
        st = _resolve_input_value(inputs.get('STRING'), blocks_dict) or '""'
        return f"довжина({st})"
    elif opcode == 'operator_mod':
        n1 = _resolve_input_value(inputs.get('NUM1'), blocks_dict) or '0'
        n2 = _resolve_input_value(inputs.get('NUM2'), blocks_dict) or '0'
        return f"({n1} mod {n2})"
    elif opcode == 'operator_round':
        num = _resolve_input_value(inputs.get('NUM'), blocks_dict) or '0'
        return f"округлити({num})"
    elif opcode == 'operator_mathop':
        oper = fields.get('OPERATOR', ['sqrt'])[0]
        num = _resolve_input_value(inputs.get('NUM'), blocks_dict) or '0'
        return f"{oper}({num})"

    # Датчики (Sensing)
    if opcode == 'sensing_touchingobject':
        menu_input = inputs.get('TOUCHINGOBJECTMENU')
        obj_name = ""
        if menu_input and isinstance(menu_input, list) and len(menu_input) > 1 and menu_input[1] in blocks_dict:
            m_block = blocks_dict[menu_input[1]]
            obj_name = m_block.get('fields', {}).get('TOUCHINGOBJECTMENU', [''])[0]
        if not obj_name:
            obj_name = fields.get('TOUCHINGOBJECTMENU', ['вказівник миші'])[0]
        if obj_name == '_mouse_': obj_name = 'вказівник миші'
        elif obj_name == '_edge_': obj_name = 'межа'
        return f"<торкається [{obj_name}]?>"

    if opcode == 'sensing_touchingcolor':
        c_val = _resolve_input_value(inputs.get('COLOR'), blocks_dict) or 'колір'
        return f"<торкається кольору {c_val}?>"

    if opcode == 'sensing_keypressed':
        key_input = inputs.get('KEY_OPTION')
        key_name = ""
        if key_input and isinstance(key_input, list) and len(key_input) > 1 and key_input[1] in blocks_dict:
            k_block = blocks_dict[key_input[1]]
            key_name = k_block.get('fields', {}).get('KEY_OPTION', [''])[0]
        if not key_name:
            key_name = fields.get('KEY_OPTION', ['пропуск'])[0]
        if key_name == 'space': key_name = 'пропуск'
        elif key_name == 'up arrow': key_name = 'стрілка вгору'
        elif key_name == 'down arrow': key_name = 'стрілка вниз'
        elif key_name == 'right arrow': key_name = 'стрілка праворуч'
        elif key_name == 'left arrow': key_name = 'стрілка ліворуч'
        elif key_name == 'any': key_name = 'будь-яка'
        return f"<клавішу [{key_name}] натиснуто?>"

    if opcode == 'sensing_distanceto':
        m_input = inputs.get('DISTANCETOMENU')
        m_name = ""
        if m_input and isinstance(m_input, list) and len(m_input) > 1 and m_input[1] in blocks_dict:
            m_block = blocks_dict[m_input[1]]
            m_name = m_block.get('fields', {}).get('DISTANCETOMENU', [''])[0]
        if not m_name:
            m_name = fields.get('DISTANCETOMENU', ['вказівник миші'])[0]
        if m_name == '_mouse_': m_name = 'вказівник миші'
        return f"відстань_до([{m_name}])"

    # Загальний шаблон
    template = OPCODE_TRANSLATIONS.get(opcode, opcode)
    for k, v in fields.items():
        if isinstance(v, list) and v:
            return f"[{v[0]}]"

    return opcode


def _decompile_block_statement(block, blocks_dict):
    """Перетворює блок інструкції (statement) у зрозумілий рядок українською мовою з підставленими параметрами."""
    if not isinstance(block, dict):
        return str(block)

    opcode = block.get('opcode', '')
    inputs = block.get('inputs', {})
    fields = block.get('fields', {})
    template = OPCODE_TRANSLATIONS.get(opcode, '')

    # 1. Події
    if opcode == 'event_whenflagclicked':
        return '🚩 Коли натиснуто 🟢 (зелений прапорець)'
    elif opcode == 'event_whenkeypressed':
        k = fields.get('KEY_OPTION', ['пропуск'])[0]
        if k == 'space': k = 'пропуск'
        elif k == 'up arrow': k = 'стрілка вгору'
        elif k == 'down arrow': k = 'стрілка вниз'
        elif k == 'right arrow': k = 'стрілка праворуч'
        elif k == 'left arrow': k = 'стрілка ліворуч'
        return f'⌨️ Коли клавішу [{k}] натиснуто'
    elif opcode == 'event_whenthisspriteclicked':
        return '🖱️ Коли цей спрайт натиснуто'
    elif opcode == 'event_whenstageclicked':
        return '🖱️ Коли сцену натиснуто'
    elif opcode == 'event_whenbroadcastreceived':
        b = fields.get('BROADCAST_OPTION', ['повідомлення'])[0]
        return f'📨 Коли я отримую повідомлення [{b}]'
    elif opcode in ('event_broadcast', 'event_broadcastandwait'):
        b_input = inputs.get('BROADCAST_INPUT')
        b_name = _resolve_input_value(b_input, blocks_dict)
        if not b_name:
            b_name = fields.get('BROADCAST_OPTION', ['повідомлення'])[0]
        suffix = " і чекати" if opcode == 'event_broadcastandwait' else ""
        return f'📢 Оповістити [{b_name}]{suffix}'

    # 2. Рух
    elif opcode == 'motion_movesteps':
        steps = _resolve_input_value(inputs.get('STEPS'), blocks_dict) or '10'
        return f'👣 Перемістити на {steps} кроків'
    elif opcode == 'motion_turnright':
        deg = _resolve_input_value(inputs.get('DEGREES'), blocks_dict) or '15'
        return f'↷ Повернути праворуч ↻ на {deg}°'
    elif opcode == 'motion_turnleft':
        deg = _resolve_input_value(inputs.get('DEGREES'), blocks_dict) or '15'
        return f'↶ Повернути ліворуч ↺ на {deg}°'
    elif opcode == 'motion_gotoxy':
        x = _resolve_input_value(inputs.get('X'), blocks_dict) or '0'
        y = _resolve_input_value(inputs.get('Y'), blocks_dict) or '0'
        return f'📍 Перемістити в x: {x}, y: {y}'
    elif opcode == 'motion_goto':
        to_in = inputs.get('TO')
        to_name = ""
        if to_in and isinstance(to_in, list) and len(to_in) > 1 and to_in[1] in blocks_dict:
            to_name = blocks_dict[to_in[1]].get('fields', {}).get('TO', [''])[0]
        if not to_name:
            to_name = fields.get('TO', ['випадкова позиція'])[0]
        if to_name == '_random_': to_name = 'випадкова позиція'
        elif to_name == '_mouse_': to_name = 'вказівник миші'
        return f'📍 Перейти до [{to_name}]'
    elif opcode == 'motion_glidesecstoxy':
        secs = _resolve_input_value(inputs.get('SECS'), blocks_dict) or '1'
        x = _resolve_input_value(inputs.get('X'), blocks_dict) or '0'
        y = _resolve_input_value(inputs.get('Y'), blocks_dict) or '0'
        return f'✈️ Ковзати {secs} сек до x: {x}, y: {y}'
    elif opcode == 'motion_pointindirection':
        direction = _resolve_input_value(inputs.get('DIRECTION'), blocks_dict) or '90'
        return f'🧭 Повернути в напрямку {direction}°'
    elif opcode == 'motion_changexby':
        dx = _resolve_input_value(inputs.get('DX'), blocks_dict) or '10'
        return f'↔️ Змінити x на {dx}'
    elif opcode == 'motion_setx':
        x = _resolve_input_value(inputs.get('X'), blocks_dict) or '0'
        return f'↔️ Встановити x в {x}'
    elif opcode == 'motion_changeyby':
        dy = _resolve_input_value(inputs.get('DY'), blocks_dict) or '10'
        return f'↕️ Змінити y на {dy}'
    elif opcode == 'motion_sety':
        y = _resolve_input_value(inputs.get('Y'), blocks_dict) or '0'
        return f'↕️ Встановити y в {y}'
    elif opcode == 'motion_ifonedgebounce':
        return '⚡ Якщо на межі, відбити'

    # 3. Вигляд
    elif opcode == 'looks_sayforsecs':
        msg = _resolve_input_value(inputs.get('MESSAGE'), blocks_dict) or '""'
        secs = _resolve_input_value(inputs.get('SECS'), blocks_dict) or '2'
        return f'💬 Говорити {msg} {secs} сек'
    elif opcode == 'looks_say':
        msg = _resolve_input_value(inputs.get('MESSAGE'), blocks_dict) or '""'
        return f'💬 Говорити {msg}'
    elif opcode == 'looks_thinkforsecs':
        msg = _resolve_input_value(inputs.get('MESSAGE'), blocks_dict) or '""'
        secs = _resolve_input_value(inputs.get('SECS'), blocks_dict) or '2'
        return f'💭 Думати {msg} {secs} сек'
    elif opcode == 'looks_think':
        msg = _resolve_input_value(inputs.get('MESSAGE'), blocks_dict) or '""'
        return f'💭 Думати {msg}'
    elif opcode == 'looks_switchcostumeto':
        c_in = inputs.get('COSTUME')
        c_name = _resolve_input_value(c_in, blocks_dict)
        if not c_name:
            c_name = fields.get('COSTUME', ['костюм'])[0]
        return f'🎭 Змінити костюм на [{c_name}]'
    elif opcode == 'looks_nextcostume':
        return '🎭 Наступний костюм'
    elif opcode == 'looks_switchbackdropto':
        b_in = inputs.get('BACKDROP')
        b_name = _resolve_input_value(b_in, blocks_dict)
        if not b_name:
            b_name = fields.get('BACKDROP', ['тло'])[0]
        return f'🖼️ Змінити тло на [{b_name}]'
    elif opcode == 'looks_nextbackdrop':
        return '🖼️ Наступне тло'
    elif opcode == 'looks_changesizeby':
        ch = _resolve_input_value(inputs.get('CHANGE'), blocks_dict) or '10'
        return f'🔍 Змінити розмір на {ch}%'
    elif opcode == 'looks_setsizeto':
        sz = _resolve_input_value(inputs.get('SIZE'), blocks_dict) or '100'
        return f'🔍 Встановити розмір {sz}%'
    elif opcode == 'looks_show':
        return '👁️ Показати'
    elif opcode == 'looks_hide':
        return '🙈 Сховати'

    # 4. Звук
    elif opcode in ('sound_playuntildone', 'sound_play'):
        s_in = inputs.get('SOUND_MENU')
        s_name = ""
        if s_in and isinstance(s_in, list) and len(s_in) > 1 and s_in[1] in blocks_dict:
            s_name = blocks_dict[s_in[1]].get('fields', {}).get('SOUND_MENU', [''])[0]
        if not s_name:
            s_name = fields.get('SOUND_MENU', ['звук'])[0]
        suffix = " до кінця" if opcode == 'sound_playuntildone' else ""
        return f'🔊 Відтворити звук [{s_name}]{suffix}'
    elif opcode == 'sound_stopallsounds':
        return '🔇 Зупинити всі звуки'

    # 5. Керування
    elif opcode == 'control_wait':
        dur = _resolve_input_value(inputs.get('DURATION'), blocks_dict) or '1'
        return f'⏳ Чекати {dur} сек'
    elif opcode == 'control_repeat':
        times = _resolve_input_value(inputs.get('TIMES'), blocks_dict) or '10'
        return f'🔁 Повторити {times} разів:'
    elif opcode == 'control_forever':
        return '🔄 Завжди:'
    elif opcode == 'control_if':
        cond = _resolve_input_value(inputs.get('CONDITION'), blocks_dict) or '<умова>'
        return f'❓ Якщо {cond}, то:'
    elif opcode == 'control_if_else':
        cond = _resolve_input_value(inputs.get('CONDITION'), blocks_dict) or '<умова>'
        return f'❓ Якщо {cond}, то:'
    elif opcode == 'control_wait_until':
        cond = _resolve_input_value(inputs.get('CONDITION'), blocks_dict) or '<умова>'
        return f'⏳ Чекати доки {cond}'
    elif opcode == 'control_repeat_until':
        cond = _resolve_input_value(inputs.get('CONDITION'), blocks_dict) or '<умова>'
        return f'🔁 Повторювати доки {cond}:'
    elif opcode == 'control_stop':
        opt = fields.get('STOP_OPTION', ['все'])[0]
        if opt == 'all': opt = 'все'
        elif opt == 'this script': opt = 'цей скрипт'
        elif opt == 'other scripts in sprite': opt = 'інші скрипти цього спрайта'
        return f'🛑 Зупинити [{opt}]'
    elif opcode == 'control_start_as_clone':
        return '👥 Коли я починаю як клон'
    elif opcode == 'control_create_clone_of':
        c_in = inputs.get('CLONE_OPTION')
        c_name = ""
        if c_in and isinstance(c_in, list) and len(c_in) > 1 and c_in[1] in blocks_dict:
            c_name = blocks_dict[c_in[1]].get('fields', {}).get('CLONE_OPTION', [''])[0]
        if not c_name:
            c_name = fields.get('CLONE_OPTION', ['себе'])[0]
        if c_name == '_myself_': c_name = 'себе'
        return f'👥 Створити клон [{c_name}]'
    elif opcode == 'control_delete_this_clone':
        return '❌ Вилучити цей клон'

    # 6. Змінні та списки
    elif opcode == 'data_setvariableto':
        var_name = fields.get('VARIABLE', ['змінна'])[0]
        val = _resolve_input_value(inputs.get('VALUE'), blocks_dict) or '0'
        return f'📊 Встановити [{var_name}] = {val}'
    elif opcode == 'data_changevariableby':
        var_name = fields.get('VARIABLE', ['змінна'])[0]
        val = _resolve_input_value(inputs.get('VALUE'), blocks_dict) or '1'
        return f'➕ Змінити [{var_name}] на {val}'
    elif opcode == 'data_showvariable':
        var_name = fields.get('VARIABLE', ['змінна'])[0]
        return f'👁️ Показати змінну [{var_name}]'
    elif opcode == 'data_hidevariable':
        var_name = fields.get('VARIABLE', ['змінна'])[0]
        return f'🙈 Сховати змінну [{var_name}]'
    elif opcode == 'data_addtolist':
        l_name = fields.get('LIST', ['список'])[0]
        itm = _resolve_input_value(inputs.get('ITEM'), blocks_dict) or '""'
        return f'📝 Додати {itm} до списку [{l_name}]'
    elif opcode == 'data_deleteoflist':
        l_name = fields.get('LIST', ['список'])[0]
        idx = _resolve_input_value(inputs.get('INDEX'), blocks_dict) or '1'
        return f'❌ Вилучити {idx} зі списку [{l_name}]'
    elif opcode == 'data_deletealloflist':
        l_name = fields.get('LIST', ['список'])[0]
        return f'❌ Вилучити все зі списку [{l_name}]'

    # 7. Власні блоки (Custom Procedures)
    elif opcode == 'procedures_definition':
        proto_in = inputs.get('custom_block')
        proto_block = blocks_dict.get(proto_in[1]) if proto_in and isinstance(proto_in, list) and len(proto_in) > 1 else None
        proc_code = "Власний блок"
        if proto_block:
            mutation = proto_block.get('mutation', {})
            proc_code = mutation.get('proccode', 'Власний блок')
        return f'🧩 Визначення власного блоку [{proc_code}]:'
    elif opcode == 'procedures_call':
        mutation = block.get('mutation', {})
        proc_code = mutation.get('proccode', 'Власний блок')
        # Збираємо передані аргументи
        args = []
        for in_k, in_v in inputs.items():
            args.append(_resolve_input_value(in_v, blocks_dict))
        args_str = f" ({', '.join(args)})" if args else ""
        return f'📞 Виклик блоку [{proc_code}]{args_str}'

    # За замовчуванням
    if template:
        return template

    return opcode


def _decompile_block_chain(start_block_id, blocks_dict, indent=0, visited=None):
    """
    Рекурсивно розбирає послідовність блоків Scratch (включаючи тіла циклів SUBSTACK та розгалужень SUBSTACK2).
    Повертає список словників: [{'text': str, 'indent': int, 'opcode': str, 'cat': str, 'bg': str, 'border': str}]
    """
    if visited is None:
        visited = set()

    result = []
    curr_id = start_block_id

    while curr_id and curr_id not in visited:
        visited.add(curr_id)
        block = blocks_dict.get(curr_id)
        if not block or not isinstance(block, dict):
            break

        opcode = block.get('opcode', '')
        _, bg_col, border_col, cat_name = _get_block_category(opcode)
        statement_text = _decompile_block_statement(block, blocks_dict)

        result.append({
            'id': curr_id,
            'text': statement_text,
            'indent': indent,
            'opcode': opcode,
            'cat': cat_name,
            'bg': bg_col,
            'border': border_col
        })

        inputs = block.get('inputs', {})

        # Обробка вкладеного тіла циклу або умови (SUBSTACK)
        if 'SUBSTACK' in inputs:
            sub_entry = inputs.get('SUBSTACK')
            if sub_entry and isinstance(sub_entry, list) and len(sub_entry) > 1:
                sub_id = sub_entry[1]
                if sub_id and sub_id in blocks_dict:
                    sub_chain = _decompile_block_chain(sub_id, blocks_dict, indent=indent + 1, visited=visited)
                    result.extend(sub_chain)

        # Обробка гілки "інакше" для if_else (SUBSTACK2)
        if 'SUBSTACK2' in inputs:
            result.append({
                'id': f"{curr_id}_else",
                'text': 'інакше:',
                'indent': indent,
                'opcode': 'control_else',
                'cat': 'Керування',
                'bg': '#f97316',
                'border': '#ea580c'
            })
            sub2_entry = inputs.get('SUBSTACK2')
            if sub2_entry and isinstance(sub2_entry, list) and len(sub2_entry) > 1:
                sub2_id = sub2_entry[1]
                if sub2_id and sub2_id in blocks_dict:
                    sub2_chain = _decompile_block_chain(sub2_id, blocks_dict, indent=indent + 1, visited=visited)
                    result.extend(sub2_chain)

        curr_id = block.get('next')

    return result


def parse_scratch_sb3(file_path, raw_file_url=None):
    """
    Розбирає файл .sb3 (Scratch 3 проєкт), рекурсивно декомпілює алгоритми
    та генерує інтерактивний HTML переглядач з TurboWarp Embed плеером.
    """
    if not file_path or not os.path.exists(file_path):
        return "", "", "Файл .sb3 не знайдено на диску."

    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            if 'project.json' not in z.namelist():
                return "", "", "Файл .sb3 пошкоджено: відсутній обов'язковий файл project.json."

            project_data_raw = z.read('project.json').decode('utf-8')
            project = json.loads(project_data_raw)

    except Exception as e:
        return "", "", f"Помилка відкриття .sb3 архіву: {str(e)}"

    targets = project.get('targets', [])
    stage = None
    sprites = []

    total_blocks_count = 0
    total_costumes_count = 0
    total_sounds_count = 0
    total_variables = []
    total_lists = []
    total_broadcasts = []

    parsed_targets = []

    for t in targets:
        is_stage = t.get('isStage', False)
        name = t.get('name', 'Сцена' if is_stage else 'Спрайт')
        blocks_dict = t.get('blocks', {})
        costumes = t.get('costumes', [])
        sounds = t.get('sounds', [])
        variables = t.get('variables', {})
        lists = t.get('lists', {})
        broadcasts = t.get('broadcasts', {})

        total_costumes_count += len(costumes)
        total_sounds_count += len(sounds)
        total_variables.extend([v[0] if isinstance(v, list) else str(v) for v in variables.values()])
        total_lists.extend([l[0] if isinstance(l, list) else str(l) for l in lists.values()])
        total_broadcasts.extend(list(broadcasts.values()))

        # Знаходимо всі кореневі (topLevel / стартові) блоки
        # У Scratch 3 стартовий блок: topLevel=True АБО (parent is None і не є shadow/reporter)
        top_block_ids = []
        regular_blocks_count = 0

        for b_id, b_data in blocks_dict.items():
            if isinstance(b_data, dict):
                regular_blocks_count += 1
                is_top = b_data.get('topLevel', False) or (b_data.get('parent') is None and not b_data.get('shadow', False))
                # Також перевіряємо hat-блоки
                opcode = b_data.get('opcode', '')
                if is_top or opcode.startswith('event_when') or opcode.startswith('control_start_as_clone') or opcode == 'procedures_definition':
                    # Перевіряємо чи він дійсно кореневий (не вкладений у SUBSTACK)
                    if b_data.get('parent') is None or b_data.get('topLevel', False):
                        if b_id not in top_block_ids:
                            top_block_ids.append(b_id)

        total_blocks_count += regular_blocks_count

        # Рекурсивно декомпілюємо ланцюжки для кожного скрипта
        scripts = []
        visited_all = set()

        for top_id in top_block_ids:
            if top_id not in visited_all:
                chain = _decompile_block_chain(top_id, blocks_dict, indent=0, visited=visited_all)
                if chain:
                    scripts.append(chain)

        # Якщо залишились блоки, які не були знайдені через top_block_ids (наприклад, розірвані ланцюжки)
        for b_id, b_data in blocks_dict.items():
            if isinstance(b_data, dict) and b_id not in visited_all and not b_data.get('shadow', False):
                # Тільки якщо це statement, а не внутрішній репортер
                opcode = b_data.get('opcode', '')
                if not opcode.startswith(('operator_', 'sensing_touching', 'sensing_key', 'data_var')):
                    chain = _decompile_block_chain(b_id, blocks_dict, indent=0, visited=visited_all)
                    if chain:
                        scripts.append(chain)

        target_info = {
            'is_stage': is_stage,
            'name': name,
            'costumes': [c.get('name', 'Костюм') for c in costumes],
            'sounds': [s.get('name', 'Звук') for s in sounds],
            'variables': [v[0] if isinstance(v, list) else str(v) for v in variables.values()],
            'lists': [l[0] if isinstance(l, list) else str(l) for l in lists.values()],
            'blocks_count': regular_blocks_count,
            'scripts': scripts,
            'visible': t.get('visible', True),
            'x': t.get('x', 0),
            'y': t.get('y', 0),
            'direction': t.get('direction', 90),
            'size': t.get('size', 100),
        }

        if is_stage:
            stage = target_info
        else:
            sprites.append(target_info)

        parsed_targets.append(target_info)

    # ── Формування детального текстового звіту для Gemini AI та детектора плагіату ──
    text_lines = [
        "=== ПОВНИЙ РОЗБІР ПРОЄКТУ SCRATCH 3 (.sb3) ===",
        f"Статистика проєкту:",
        f"- Сцена та спрайтів: {len(targets)} (Спрайтів: {len(sprites)})",
        f"- Всього блоків інструкцій: {total_blocks_count}",
        f"- Костюмів та фонів: {total_costumes_count}",
        f"- Звукових ефектів: {total_sounds_count}",
        f"- Глобальні та локальні змінні: {', '.join(set(total_variables)) if total_variables else 'немає'}",
        f"- Списки даних: {', '.join(set(total_lists)) if total_lists else 'немає'}",
        f"- Повідомлення (broadcasts): {', '.join(set(total_broadcasts)) if total_broadcasts else 'немає'}",
        "\n--- ДЕТАЛЬНА ДЕКОМПІЛЯЦІЯ СПРАЙТІВ ТА АЛГОРИТМІВ ---"
    ]

    for t in parsed_targets:
        t_type = "СЦЕНА" if t['is_stage'] else "СПРАЙТ"
        text_lines.append(f"\n[{t_type}: «{t['name']}»] (Блоків: {t['blocks_count']}, Костюмів: {len(t['costumes'])}, Звуків: {len(t['sounds'])})")
        if t['costumes']:
            text_lines.append(f"  Костюми: {', '.join(t['costumes'])}")
        if t['sounds']:
            text_lines.append(f"  Звуки: {', '.join(t['sounds'])}")
        if t['variables']:
            text_lines.append(f"  Змінні: {', '.join(t['variables'])}")

        if t['scripts']:
            text_lines.append(f"  Алгоритмічні скрипти ({len(t['scripts'])} блокових ланцюжків):")
            for s_idx, sc in enumerate(t['scripts'], 1):
                text_lines.append(f"    --- Скрипт #{s_idx} ({len(sc)} блоків) ---")
                for b in sc:
                    ind = "  " * (b['indent'] + 2)
                    text_lines.append(f"{ind}▶ {b['text']}")
        else:
            text_lines.append("  (Скрипти у цього об'єкта відсутні)")

    text_summary = "\n".join(text_lines)

    # ── Формування інтерактивного HTML інтерфейсу ───────────────────────────────
    html_parts = []
    html_parts.append('<div class="scratch-project-viewer" style="font-family:inherit;color:var(--color-text-primary);padding:4px 0;">')

    # Формуємо прямі посилання на TurboWarp з параметром project_url (якщо передано URL)
    turbowarp_player_url = ""
    turbowarp_editor_url = "https://turbowarp.org/editor"
    if raw_file_url:
        encoded_url = urllib.parse.quote(raw_file_url, safe='')
        turbowarp_player_url = f"https://turbowarp.org/embed?project_url={encoded_url}"
        turbowarp_editor_url = f"https://turbowarp.org/editor?project_url={encoded_url}"

    # Головна шапка
    html_parts.append(
        '<div style="background:linear-gradient(135deg,rgba(245,158,11,0.12),rgba(234,88,12,0.08));border:1.5px solid rgba(245,158,11,0.4);border-radius:var(--radius-lg);padding:18px 22px;margin-bottom:20px;">'
        '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:14px;">'
        '<div style="display:flex;align-items:center;gap:10px;">'
        '<span style="font-size:32px;">🐱</span>'
        '<div>'
        '<h3 style="margin:0;font-size:17px;font-weight:800;color:var(--color-text-primary);">Проєкт Scratch 3 (.sb3)</h3>'
        '<div style="font-size:12px;color:var(--color-text-muted);margin-top:2px;">Повна декомпіляція алгоритмів, вкладених циклів та умов Scratch 3</div>'
        '</div>'
        '</div>'
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">'
    )

    if raw_file_url:
        html_parts.append(
            f'<button type="button" class="btn btn-primary btn-sm" onclick="toggleScratchLivePlayer()" style="font-size:12px;font-weight:800;background:#ea580c;border-color:#ea580c;color:#fff;">'
            '🎮 Запустити гру прямо тут'
            '</button>'
            f'<a href="{turbowarp_editor_url}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm" style="font-size:11.5px;font-weight:700;background:var(--color-surface);">'
            '⚡ Відкрити в TurboWarp з кодом ↗'
            '</a>'
        )
    else:
        html_parts.append(
            '<a href="https://turbowarp.org/editor" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm" style="font-size:11.5px;font-weight:700;background:var(--color-surface);">'
            '⚡ TurboWarp Редактор ↗'
            '</a>'
        )

    html_parts.append(
        '<a href="https://scratch.mit.edu/projects/editor/" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm" style="font-size:11.5px;font-weight:700;background:var(--color-surface);">'
        '🐱 Scratch MIT ↗'
        '</a>'
        '</div>'
        '</div>'
    )

    # Метрики проєкту
    html_parts.append(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(130px, 1fr));gap:10px;">'
        f'<div style="background:var(--color-surface);padding:10px 14px;border-radius:8px;border:1px solid var(--color-border);text-align:center;">'
        f'<div style="font-size:11px;color:var(--color-text-muted);font-weight:700;text-transform:uppercase;">🎭 Спрайтів</div>'
        f'<div style="font-size:20px;font-weight:800;color:var(--color-primary);margin-top:2px;">{len(sprites)}</div>'
        f'</div>'
        f'<div style="background:var(--color-surface);padding:10px 14px;border-radius:8px;border:1px solid var(--color-border);text-align:center;">'
        f'<div style="font-size:11px;color:var(--color-text-muted);font-weight:700;text-transform:uppercase;">🧱 Блоків коду</div>'
        f'<div style="font-size:20px;font-weight:800;color:#f59e0b;margin-top:2px;">{total_blocks_count}</div>'
        f'</div>'
        f'<div style="background:var(--color-surface);padding:10px 14px;border-radius:8px;border:1px solid var(--color-border);text-align:center;">'
        f'<div style="font-size:11px;color:var(--color-text-muted);font-weight:700;text-transform:uppercase;">👗 Костюмів</div>'
        f'<div style="font-size:20px;font-weight:800;color:#10b981;margin-top:2px;">{total_costumes_count}</div>'
        f'</div>'
        f'<div style="background:var(--color-surface);padding:10px 14px;border-radius:8px;border:1px solid var(--color-border);text-align:center;">'
        f'<div style="font-size:11px;color:var(--color-text-muted);font-weight:700;text-transform:uppercase;">🔊 Звуків</div>'
        f'<div style="font-size:20px;font-weight:800;color:#ec4899;margin-top:2px;">{total_sounds_count}</div>'
        f'</div>'
        f'<div style="background:var(--color-surface);padding:10px 14px;border-radius:8px;border:1px solid var(--color-border);text-align:center;">'
        f'<div style="font-size:11px;color:var(--color-text-muted);font-weight:700;text-transform:uppercase;">📊 Змінних</div>'
        f'<div style="font-size:20px;font-weight:800;color:#8b5cf6;margin-top:2px;">{len(set(total_variables))}</div>'
        f'</div>'
        '</div>'
        '</div>'
    )

    # Інтерактивний вбудований плеєр Scratch (Local Scaffolding Runner 60 FPS)
    if raw_file_url:
        html_parts.append(
            '<div id="scratch-live-player-container" style="display:none;margin-bottom:24px;background:#111827;border:1.5px solid rgba(234,88,12,0.4);border-radius:var(--radius-lg);overflow:hidden;box-shadow:0 12px 36px rgba(0,0,0,0.5);">'
            '<!-- Верхня панель керування -->'
            '<div style="padding:10px 16px;background:#1f2937;border-bottom:1px solid rgba(255,255,255,0.1);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">'
            '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">'
            '<span style="font-size:13.5px;font-weight:800;color:#f97316;display:flex;align-items:center;gap:6px;">'
            '<span>🎮</span> <span>Scratch 3 Player (Локальний рушій 60 FPS)</span>'
            '</span>'
            '<div style="display:inline-flex;align-items:center;gap:6px;">'
            '<button type="button" onclick="scratchPlayerGreenFlag()" class="btn btn-sm" style="background:#16a34a;color:#fff;border:none;font-weight:800;padding:4px 10px;border-radius:6px;font-size:12px;" title="Запустити проєкт (Зелений прапорець)">🟢 Старт</button>'
            '<button type="button" onclick="scratchPlayerStop()" class="btn btn-sm" style="background:#dc2626;color:#fff;border:none;font-weight:800;padding:4px 10px;border-radius:6px;font-size:12px;" title="Зупинити проєкт">🔴 Стоп</button>'
            '<button type="button" onclick="scratchPlayerRestart()" class="btn btn-secondary btn-sm" style="font-size:11.5px;padding:4px 8px;background:rgba(255,255,255,0.1);border-color:rgba(255,255,255,0.2);color:#fff;" title="Перезапустити проєкт">🔄</button>'
            '<button type="button" onclick="scratchPlayerFullscreen()" class="btn btn-secondary btn-sm" style="font-size:11.5px;padding:4px 8px;background:rgba(255,255,255,0.1);border-color:rgba(255,255,255,0.2);color:#fff;" title="На весь екран">⛶</button>'
            '</div>'
            '</div>'
            '<div style="display:flex;align-items:center;gap:8px;">'
            '<span id="scratch-player-status" style="font-size:11.5px;color:#9ca3af;font-weight:600;">Готовий до запуску</span>'
            '<button type="button" onclick="toggleScratchLivePlayer()" class="btn btn-secondary btn-sm" style="font-size:11px;padding:3px 8px;">✕ Закрити</button>'
            '</div>'
            '</div>'
            '<!-- Область візуалізації Scratch сцени -->'
            '<div id="scratch-canvas-wrapper" style="position:relative;width:100%;height:460px;background:#000;display:flex;align-items:center;justify-content:center;overflow:hidden;">'
            '<div id="scratch-loading-indicator" style="display:flex;flex-direction:column;align-items:center;gap:12px;color:#fff;">'
            '<div class="spinner-border text-warning" role="status" style="width:2.5rem;height:2.5rem;"></div>'
            '<div style="font-weight:700;font-size:13.5px;">Завантаження та запуск проєкту...</div>'
            '</div>'
            '</div>'
            '</div>'
        )

    # Список об'єктів та декомпільованих скриптів
    html_parts.append('<div style="display:flex;flex-direction:column;gap:16px;">')

    for t in parsed_targets:
        is_stg = t['is_stage']
        icon = '🖼️' if is_stg else '🐱'
        t_label = 'Сцена' if is_stg else 'Спрайт'
        border_color = 'rgba(99,102,241,0.4)' if is_stg else 'var(--color-border)'

        html_parts.append(
            f'<div style="background:var(--color-surface);border:1.5px solid {border_color};border-radius:var(--radius-lg);overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,0.04);">'
            f'<div style="padding:14px 18px;background:var(--color-bg-secondary);border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">'
            f'<div style="display:flex;align-items:center;gap:10px;">'
            f'<span style="font-size:24px;">{icon}</span>'
            f'<div>'
            f'<span style="font-size:11px;font-weight:800;text-transform:uppercase;color:var(--color-primary);letter-spacing:0.04em;">{t_label}</span>'
            f'<h4 style="margin:0;font-size:15px;font-weight:800;color:var(--color-text-primary);">{html.escape(t["name"])}</h4>'
            f'</div>'
            f'</div>'
            f'<div style="display:flex;gap:6px;font-size:11.5px;font-weight:700;flex-wrap:wrap;">'
            f'<span class="badge" style="background:rgba(245,158,11,0.12);color:#b45309;">🧱 {t["blocks_count"]} блоків</span>'
            f'<span class="badge" style="background:rgba(16,185,129,0.12);color:#059669;">👗 {len(t["costumes"])} костюмів</span>'
            f'<span class="badge" style="background:rgba(236,72,153,0.12);color:#be185d;">🔊 {len(t["sounds"])} звуків</span>'
            f'</div>'
            f'</div>'
        )

        html_parts.append('<div style="padding:16px 20px;">')

        # Скрипти спрайта з вкладеними відступами
        if t['scripts']:
            html_parts.append('<div style="margin-bottom:12px;font-size:12px;font-weight:800;color:var(--color-text-muted);text-transform:uppercase;letter-spacing:0.04em;">📜 Алгоритми та блоки коду:</div>')
            html_parts.append('<div style="display:flex;flex-direction:column;gap:14px;">')

            for s_idx, chain in enumerate(t['scripts'], 1):
                html_parts.append(
                    f'<div style="background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:8px;padding:12px 14px;">'
                    f'<div style="font-size:11px;font-weight:800;color:var(--color-text-muted);margin-bottom:8px;">▶ Скрипт #{s_idx} ({len(chain)} блоків)</div>'
                    f'<div style="display:flex;flex-direction:column;gap:4px;">'
                )

                for b in chain:
                    indent_px = b['indent'] * 20
                    is_else = b['opcode'] == 'control_else'
                    font_weight = '800' if b['indent'] == 0 else '600'
                    opacity = '1'

                    html_parts.append(
                        f'<div style="display:flex;align-items:center;margin-left:{indent_px}px;">'
                        f'<div style="display:inline-flex;align-items:center;background:{b["bg"]}18;border-left:4px solid {b["bg"]};border-radius:4px;padding:5px 10px;font-size:12.5px;font-weight:{font_weight};color:var(--color-text-primary);word-break:break-word;max-width:100%;">'
                        f'<span>{html.escape(b["text"])}</span>'
                        f'</div>'
                        f'</div>'
                    )

                html_parts.append('</div></div>')

            html_parts.append('</div>')
        else:
            html_parts.append('<div style="color:var(--color-text-muted);font-size:13px;font-style:italic;padding:8px 0;">У цього об\'єкта немає доданих блоків коду.</div>')

        # Список костюмів та звуків
        if t['costumes'] or t['sounds']:
            html_parts.append('<div style="margin-top:14px;padding-top:12px;border-top:1px dashed var(--color-border);display:flex;gap:16px;flex-wrap:wrap;font-size:12px;">')
            if t['costumes']:
                html_parts.append(f'<div><strong style="color:var(--color-text-primary);">👗 Костюми:</strong> <span style="color:var(--color-text-muted);">{html.escape(", ".join(t["costumes"]))}</span></div>')
            if t['sounds']:
                html_parts.append(f'<div><strong style="color:var(--color-text-primary);">🔊 Звуки:</strong> <span style="color:var(--color-text-muted);">{html.escape(", ".join(t["sounds"]))}</span></div>')
            html_parts.append('</div>')

        html_parts.append('</div></div>')

    html_parts.append('</div>')  # /.targets list

    # Вихідний project.json для розробників
    html_parts.append(
        '<div style="margin-top:20px;border-top:1px dashed var(--color-border);padding-top:14px;">'
        '<details style="background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--radius-md);padding:10px 14px;">'
        '<summary style="cursor:pointer;font-weight:700;font-size:12.5px;color:var(--color-primary);">🔍 Переглянути вихідний project.json (структура даних Scratch 3)</summary>'
        f'<pre style="margin-top:10px;padding:12px;background:#1e1e2e;color:#f8fafc;border-radius:6px;font-family:\'JetBrains Mono\',monospace;font-size:11.5px;overflow-x:auto;max-height:400px;white-space:pre-wrap;"><code>{html.escape(json.dumps(project, indent=2, ensure_ascii=False))}</code></pre>'
        '</details>'
        '</div>'
    )

    # JS логіка інтерактивного Scratch плеєра
    html_parts.append(
        '<script>'
        'var _scratchScaffInstance = null;'
        'var _scratchLoaded = false;'
        'var _scratchRawUrl = "' + (raw_file_url or "") + '";'
        '\n'
        'function loadScratchScaffolding() {'
        '  if (window.Scaffolding && (window.Scaffolding.Scaffolding || typeof window.Scaffolding === "function")) {'
        '    return Promise.resolve(window.Scaffolding.Scaffolding || window.Scaffolding);'
        '  }'
        '  return new Promise(function(resolve, reject) {'
        '    var s = document.createElement("script");'
        '    s.src = "/static/vendor/turbowarp/scaffolding-min.js";'
        '    s.onload = function() {'
        '      var Scaff = window.Scaffolding && (window.Scaffolding.Scaffolding || window.Scaffolding);'
        '      if (Scaff) resolve(Scaff); else reject(new Error("Не знайдено Scaffolding"));'
        '    };'
        '    s.onerror = function() {'
        '      var cdn = document.createElement("script");'
        '      cdn.src = "https://cdn.jsdelivr.net/npm/@turbowarp/scaffolding@0.4.0/dist/scaffolding-min.js";'
        '      cdn.onload = function() {'
        '        var Scaff = window.Scaffolding && (window.Scaffolding.Scaffolding || window.Scaffolding);'
        '        if (Scaff) resolve(Scaff); else reject(new Error("Не знайдено Scaffolding (CDN)"));'
        '      };'
        '      cdn.onerror = function() { reject(new Error("Помилка завантаження рушія Scratch")); };'
        '      document.head.appendChild(cdn);'
        '    };'
        '    document.head.appendChild(s);'
        '  });'
        '}'
        '\n'
        'function toggleScratchLivePlayer() {'
        '  var box = document.getElementById("scratch-live-player-container");'
        '  if (!box) return;'
        '  if (box.style.display === "none") {'
        '    box.style.display = "block";'
        '    box.scrollIntoView({ behavior: "smooth", block: "nearest" });'
        '    if (!_scratchLoaded) {'
        '      runLocalScratchPlayer();'
        '    }'
        '  } else {'
        '    box.style.display = "none";'
        '    if (_scratchScaffInstance) { try { _scratchScaffInstance.stopAll(); } catch(e){} }'
        '  }'
        '}'
        '\n'
        'function runLocalScratchPlayer() {'
        '  var statusEl = document.getElementById("scratch-player-status");'
        '  var wrapper = document.getElementById("scratch-canvas-wrapper");'
        '  if (!_scratchRawUrl) return;'
        '  if (statusEl) statusEl.textContent = "⏳ Завантаження рушія...";'
        '  loadScratchScaffolding().then(function(ScaffClass) {'
        '    if (statusEl) statusEl.textContent = "📦 Отримання .sb3 проєкту...";'
        '    return fetch(_scratchRawUrl).then(function(res) {'
        '      if (!res.ok) throw new Error("HTTP помилка (" + res.status + " " + res.statusText + ")");'
        '      return res.arrayBuffer();'
        '    }).then(function(buffer) {'
        '      if (statusEl) statusEl.textContent = "⚙️ Компіляція Scratch коду...";'
        '      wrapper.innerHTML = "";'
        '      var scaff = new ScaffClass();'
        '      scaff.width = 480;'
        '      scaff.height = 360;'
        '      scaff.resizeMode = "preserve-ratio";'
        '      scaff.editableLists = false;'
        '      scaff.setup();'
        '      scaff.appendTo(wrapper);'
        '      return scaff.loadProject(buffer).then(function() {'
        '        scaff.greenFlag();'
        '        _scratchScaffInstance = scaff;'
        '        _scratchLoaded = true;'
        '        if (statusEl) statusEl.textContent = "🟢 Працює";'
        '      });'
        '    });'
        '  }).catch(function(err) {'
        '    console.error("Local Scratch player error:", err);'
        '    if (statusEl) statusEl.textContent = "❌ Помилка запуску";'
        '    if (wrapper) {'
        '      wrapper.innerHTML = "<div style=\\"padding:24px;text-align:center;color:#ef4444;max-width:520px;\\">"'
        '        + "<div style=\\"font-size:32px;margin-bottom:8px;\\">⚠️</div>"'
        '        + "<div style=\\"font-weight:800;font-size:15px;margin-bottom:6px;\\">Помилка локального запуску Scratch</div>"'
        '        + "<div style=\\"font-size:12.5px;color:#cbd5e1;margin-bottom:14px;\\">" + (err.message || err) + "</div>"'
        '        + "<div style=\\"display:flex;gap:8px;justify-content:center;\\">"'
        '        + "<a href=\\"" + _scratchRawUrl + "\\" download class=\\"btn btn-primary btn-sm\\" style=\\"font-size:12px;background:#ea580c;border-color:#ea580c;\\">⬇️ Завантажити .sb3 файл</a>"'
        '        + "<button type=\\"button\\" onclick=\\"runLocalScratchPlayer()\\" class=\\"btn btn-secondary btn-sm\\" style=\\"font-size:12px;\\">🔄 Спробувати знову</button>"'
        '        + "</div></div>";'
        '    }'
        '  });'
        '}'
        '\n'
        'function scratchPlayerGreenFlag() {'
        '  if (_scratchScaffInstance) {'
        '    _scratchScaffInstance.greenFlag();'
        '    var s = document.getElementById("scratch-player-status");'
        '    if (s) s.textContent = "🟢 Працює";'
        '  }'
        '}'
        '\n'
        'function scratchPlayerStop() {'
        '  if (_scratchScaffInstance) {'
        '    _scratchScaffInstance.stopAll();'
        '    var s = document.getElementById("scratch-player-status");'
        '    if (s) s.textContent = "🔴 Зупинено";'
        '  }'
        '}'
        '\n'
        'function scratchPlayerRestart() {'
        '  if (_scratchScaffInstance) {'
        '    _scratchScaffInstance.stopAll();'
        '    _scratchScaffInstance.greenFlag();'
        '    var s = document.getElementById("scratch-player-status");'
        '    if (s) s.textContent = "🟢 Перезапущено";'
        '  }'
        '}'
        '\n'
        'function scratchPlayerFullscreen() {'
        '  var w = document.getElementById("scratch-canvas-wrapper");'
        '  if (w) {'
        '    if (!document.fullscreenElement) {'
        '      if (w.requestFullscreen) w.requestFullscreen();'
        '      else if (w.webkitRequestFullscreen) w.webkitRequestFullscreen();'
        '    } else {'
        '      if (document.exitFullscreen) document.exitFullscreen();'
        '    }'
        '  }'
        '}'
        '</script>'
    )

    html_parts.append('</div>')  # /.scratch-project-viewer

    return "".join(html_parts), text_summary, None
