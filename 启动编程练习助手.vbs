' ============================================================
'  编程练习助手 —— 双击启动
' ============================================================
'
'  为什么是 .vbs 而不是 .bat：
'  .bat 双击会先弹出一个命令行黑框，用户明确要求不要黑框。
'  .vbs 由 wscript.exe 执行，窗口参数设为 0（隐藏），
'  所以双击后直接出应用界面，全程看不到终端。
'
'  2026-09-23 改：不再启动 desktop 目录下的 electron 运行时，
'  改为启动 dist-app-final\CodeLab.exe —— 那是一个已经组装好的
'  独立应用目录（自带运行时 + 界面 + 后端），不依赖 node_modules，
'  也不会因为源码目录被改动而失效。
'
'  其实直接双击 CodeLab.exe 就能启动（它是 GUI 程序，本身不弹
'  黑框）。这个脚本只是给「想放桌面」的场景用的。
' ============================================================

Option Explicit

Dim sh, fso, appDir, exePath

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir  = "D:\BaiduNetdiskDownload\AlgoForge\dist-app-final"
exePath = appDir & "\CodeLab.exe"

' 路径检查：给出能看懂的提示，而不是静默失败。
' 这正是之前「点了没反应」最难受的地方 —— 什么都没发生，
' 也没有任何线索说为什么。
If Not fso.FileExists(exePath) Then
  MsgBox "找不到应用：" & vbCrLf & vbCrLf & exePath & vbCrLf & vbCrLf & _
         "如果项目被移动过，请用记事本打开这个文件，把 appDir 改到新位置。", _
         16, "启动失败"
  WScript.Quit 1
End If

' 工作目录设成 exe 所在目录：应用启动 PostgreSQL 时要用相对路径
' 找 D:\pg，从别处启动会找不到数据目录。
sh.CurrentDirectory = appDir
' 参数 0 = 隐藏窗口；False = 不等待它结束。
sh.Run """" & exePath & """", 0, False
