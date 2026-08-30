Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c start_register.bat", 0, False
Set WshShell = Nothing
