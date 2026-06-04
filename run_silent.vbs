' Launches the Steam Price Tracker server silently (no console window).
' Used by the auto-start-on-login shortcut. Run start.bat once first so
' the Python dependencies are installed.

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = scriptDir & "\backend"

' 0 = hidden window, False = don't wait for it to finish
sh.Run "python -m uvicorn main:app --host 0.0.0.0 --port 8770", 0, False
