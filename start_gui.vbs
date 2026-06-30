Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDir

pythonw = appDir & "\.venv\Scripts\pythonw.exe"
script = appDir & "\gui_clicker.py"

If Not fso.FileExists(pythonw) Then
    MsgBox "Python environment not found. Run setup.cmd first.", 48, "Cookie Run Classic Runner"
    WScript.Quit 1
End If

shell.Run """" & pythonw & """ """ & script & """", 0, False
