# Thin wrapper: the canonical runner is scripts/baseline_suite.py (audit A-029).
# Usage: .\scripts\baseline_suite.ps1 [-Out data/baseline] [extra pytest-runner args]
param(
    [string]$Out = "data/baseline",
    [Parameter(ValueFromRemainingArguments = $true)] [string[]]$Rest
)
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $root ".venv-verify\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = Join-Path $root ".venv\Scripts\python.exe" }
if (-not (Test-Path $py)) { $py = "python" }
& $py (Join-Path $root "scripts\baseline_suite.py") --out $Out @Rest
exit $LASTEXITCODE
