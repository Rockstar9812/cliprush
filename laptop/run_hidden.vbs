' Runs ClipRush silently (no black window popping up).
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run """" & here & "\run_laptop.bat""", 0, False
