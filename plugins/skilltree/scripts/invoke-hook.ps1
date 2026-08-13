param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop')]
    [string]$EventName
)

$ErrorActionPreference = 'Stop'
if (-not $env:PLUGIN_DATA -or -not $env:PLUGIN_ROOT) {
    exit 0
}

$python = Join-Path $env:PLUGIN_DATA 'venv\Scripts\python.exe'
$handler = Join-Path $env:PLUGIN_ROOT 'runtime\skilltree_hook.py'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $handler)) {
    exit 0
}

try {
    & $python $handler $EventName
} catch {
    exit 0
}
