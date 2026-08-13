param(
    [string]$PluginData = $env:PLUGIN_DATA
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($PluginData)) {
    throw 'PLUGIN_DATA is required. Run this script from the installed Codex Plugin context.'
}

$runtimeRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $PluginData 'venv'
if (-not (Test-Path -LiteralPath (Join-Path $venvPath 'Scripts\python.exe'))) {
    py -3 -m venv $venvPath
}

$python = Join-Path $venvPath 'Scripts\python.exe'
& $python -m pip install --disable-pip-version-check --require-hashes -r (Join-Path $runtimeRoot 'requirements.lock')
Write-Output 'SkillTree runtime created. Review and trust hooks in Codex /hooks before enabling trace capture.'
