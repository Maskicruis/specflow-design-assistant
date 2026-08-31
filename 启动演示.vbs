Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
python = root & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(python) Then
  shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & root & "\launcher.py" & Chr(34), 0, False
Else
  MsgBox "Python environment missing. Please run dependency setup first."
End If
