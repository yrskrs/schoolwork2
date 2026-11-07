Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
LauncherDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = LauncherDir
WshShell.Run Chr(34) & FSO.BuildPath(LauncherDir, "start.bat") & Chr(34), 0, False
Set FSO = Nothing
Set WshShell = Nothing
