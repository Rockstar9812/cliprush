' ClipRush background loop: runs one cycle, waits 30 minutes, repeats. Hidden, no admin needed.
' Started at sign-in from your Startup folder. A lock file stops two copies running at once.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
lock = here & "\loop.lock"
If fso.FileExists(lock) Then
  If DateDiff("n", fso.GetFile(lock).DateLastModified, Now) < 35 Then WScript.Quit
End If
Set sh = CreateObject("WScript.Shell")
Do
  Set f = fso.CreateTextFile(lock, True) : f.WriteLine Now : f.Close
  sh.Run """" & here & "\run_laptop.bat""", 0, True
  Set f = fso.CreateTextFile(lock, True) : f.WriteLine Now : f.Close
  WScript.Sleep 1800000
Loop
