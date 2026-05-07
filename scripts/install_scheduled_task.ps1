param(
    [string]$TaskName = "X1 BiliNote Knowledge Flow",
    [string]$Time = "23:30"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = (Get-Command python).Source
$Runner = Join-Path $RepoRoot "scripts\bilinote_workflow.py"
$Action = "`"$Python`" `"$Runner`" run"

schtasks /Create /F /SC DAILY /ST $Time /TN $TaskName /TR $Action
