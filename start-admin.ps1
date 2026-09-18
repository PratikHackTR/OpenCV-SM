$ErrorActionPreference = 'Stop'
$controllerPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $controllerPython)) {
    throw 'Run setup.cmd first.'
}
Start-Process -FilePath $controllerPython -ArgumentList '-m', 'webmotion.app' -WorkingDirectory $PSScriptRoot -Verb RunAs
