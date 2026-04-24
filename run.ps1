$python = "C:\Users\a1987\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path $python)) {
    throw "未找到工作区自带 Python 运行时：$python"
}

Set-Location $PSScriptRoot
& $python "app.py"
