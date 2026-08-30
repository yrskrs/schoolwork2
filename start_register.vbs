' ============================================================================
' Шкільні Завдання (SchoolNet) — Безконсольний запуск вікна реєстрації (Windows VBS)
' ============================================================================
' Запускає start_register.bat приховано (без миготіння консолі)
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c start_register.bat", 0, False
Set WshShell = Nothing

