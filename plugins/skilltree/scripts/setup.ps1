param(
    [Parameter(Mandatory = $true)][string]$PluginData,
    [Parameter(Mandatory = $true)][string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$pluginRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$statePath = Join-Path $PluginData 'runtime-state.json'
$env:PYTHONPATH = ''
$env:PYTHONHOME = ''

function Write-SetupResult([string]$Status, [int]$ExitCode, [string]$ErrorCode = '') {
    if ($ErrorCode) {
        @{ schema_version = 'skilltree-setup/v1'; status = $Status; error = @{ code = $ErrorCode; message = $ErrorCode } } | ConvertTo-Json -Compress
    } else {
        $manifest = Get-Content -Raw (Join-Path $pluginRoot 'runtime\bundle-manifest.json') | ConvertFrom-Json
        @{ schema_version = 'skilltree-setup/v1'; status = $Status; bundle_hash = $manifest.bundle_hash } | ConvertTo-Json -Compress
    }
    exit $ExitCode
}

function Test-AbsolutePath([string]$Path) {
    return $Path -match '^(?:[A-Za-z]:\\|\\\\)'
}

function Test-PathWithin([string]$Child, [string]$Parent) {
    $normalizedChild = [IO.Path]::GetFullPath($Child).TrimEnd([char[]]'\\/')
    $normalizedParent = [IO.Path]::GetFullPath($Parent).TrimEnd([char[]]'\\/')
    return $normalizedChild.Equals($normalizedParent, [StringComparison]::OrdinalIgnoreCase) -or $normalizedChild.StartsWith($normalizedParent + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Write-CliShim([string]$DataDir) {
    $binDir = Join-Path $DataDir 'bin'
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    $shimText = @"
@echo off
setlocal
"%~dp0..\venv\Scripts\python.exe" -I -m skilltree %*
exit /b %ERRORLEVEL%
"@
    foreach ($name in @('skilltree.cmd', 'skilltree-cli.cmd')) {
        [IO.File]::WriteAllText((Join-Path $binDir $name), $shimText, [Text.UTF8Encoding]::new($false))
    }
}

function Register-UserCliPath([string]$DataDir) {
    $binDir = [IO.Path]::GetFullPath((Join-Path $DataDir 'bin')).TrimEnd([char[]]'\/')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @()
    if ($userPath) {
        $entries = @($userPath -split ';' | Where-Object { $_ })
        # Recover paths written without separators by older setup/test runs.
        if ($entries.Count -eq 1 -and $entries[0] -match '^[A-Za-z]:\\.*[A-Za-z]:\\') {
            $entries = @($entries[0] -split '(?=[A-Za-z]:\\)' | Where-Object { $_ })
        }
        $entries = @($entries | Where-Object {
            $_ -notmatch '\\AppData\\Local\\Temp\\tmp[^\\]+\\plugin-data\\bin$'
        })
    }
    if (-not ($entries | Where-Object { $_.TrimEnd('\') -ieq $binDir })) {
        $entries = @($entries) + $binDir
    }
    $normalizedPath = $entries -join ';'
    if ($userPath -ne $normalizedPath) {
        [Environment]::SetEnvironmentVariable('Path', $normalizedPath, 'User')
    }
}

function Register-UserRuntimeData([string]$DataDir) {
    $normalizedDataDir = [IO.Path]::GetFullPath($DataDir).TrimEnd([char[]]'\/')
    if ([Environment]::GetEnvironmentVariable('SKILLTREE_DATA_DIR', 'User') -ne $normalizedDataDir) {
        [Environment]::SetEnvironmentVariable('SKILLTREE_DATA_DIR', $normalizedDataDir, 'User')
    }
}

$rollbackVenv = $null
$oldVenvMoved = $false
$venvSwitched = $false
$databaseCreatedByThisInstall = $false

try {
    if (-not (Test-AbsolutePath $PluginData) -or -not (Test-AbsolutePath $PythonPath)) { Write-SetupResult 'failed' 2 'invalid_argument' }
    $python = [IO.Path]::GetFullPath($PythonPath)
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { Write-SetupResult 'failed' 2 'invalid_bootstrap_python' }
    $dataDir = [IO.Path]::GetFullPath($PluginData)
    $workspace = [IO.Path]::GetFullPath((Get-Location).Path)
    if ((Test-PathWithin $dataDir $pluginRoot) -or (Test-PathWithin $pluginRoot $dataDir) -or (Test-PathWithin $dataDir $workspace) -or (Test-PathWithin $workspace $dataDir)) { Write-SetupResult 'failed' 2 'invalid_argument' }
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    & $python -I (Join-Path $pluginRoot 'runtime\bundle_validate.py') $pluginRoot *> $null
    if ($LASTEXITCODE -ne 0) { Write-SetupResult 'failed' 3 'bundle_validation_failed' }
    & $python -I -c "import sys,venv; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" *> $null
    if ($LASTEXITCODE -ne 0) { Write-SetupResult 'failed' 2 'invalid_bootstrap_python' }

    $manifest = Get-Content -Raw (Join-Path $pluginRoot 'runtime\bundle-manifest.json') | ConvertFrom-Json
    $venvPath = Join-Path $dataDir 'venv'
    $databasePath = Join-Path $dataDir 'skilltree.sqlite3'
    $databaseCreatedByThisInstall = -not (Test-Path -LiteralPath $databasePath)
    if ((Test-Path -LiteralPath $statePath) -and (Test-Path -LiteralPath (Join-Path $venvPath 'Scripts\python.exe'))) {
        try {
            $state = Get-Content -Raw $statePath | ConvertFrom-Json
            & (Join-Path $venvPath 'Scripts\python.exe') -I -c "import skilltree" *> $null
            if ($state.bundle_hash -eq $manifest.bundle_hash -and $LASTEXITCODE -eq 0) { Write-SetupResult 'already_installed' 0 }
        } catch {
            # An invalid prior state is not a valid runtime; proceed through staging.
        }
    }

    $staging = Join-Path $dataDir ('install-staging\' + [guid]::NewGuid().ToString('N'))
    $stagedVenv = Join-Path $staging 'venv'
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    & $python -m venv $stagedVenv *> $null
    if ($LASTEXITCODE -ne 0) { Write-SetupResult 'failed' 4 'offline_install_failed' }
    $stagedPython = Join-Path $stagedVenv 'Scripts\python.exe'
    & $stagedPython -m pip install --quiet --no-index --require-hashes --find-links (Join-Path $pluginRoot 'runtime\wheels') -r (Join-Path $pluginRoot 'requirements.lock') *> $null
    if ($LASTEXITCODE -ne 0) { Write-SetupResult 'failed' 4 'offline_install_failed' }
    & $stagedPython -I -c "import skilltree" *> $null
    if ($LASTEXITCODE -ne 0) { Write-SetupResult 'failed' 5 'smoke_check_failed' }
    & $stagedPython -I -m skilltree storage initialize --data-dir $dataDir --plugin-root $pluginRoot --target-schema-version $manifest.schema.migration_version --json *> $null
    if ($LASTEXITCODE -ne 0) {
        if ($databaseCreatedByThisInstall) {
            foreach ($databaseArtifact in @($databasePath, ($databasePath + '-wal'), ($databasePath + '-shm'))) {
                if (Test-Path -LiteralPath $databaseArtifact) { Remove-Item -LiteralPath $databaseArtifact -Force }
            }
        }
        Write-SetupResult 'failed' 6 'database_initialize_failed'
    }

    $rollbackVenv = Join-Path $dataDir ('rollback\' + [guid]::NewGuid().ToString('N'))
    if (Test-Path -LiteralPath $venvPath) {
        New-Item -ItemType Directory -Force -Path (Split-Path $rollbackVenv) | Out-Null
        Move-Item -LiteralPath $venvPath -Destination $rollbackVenv
        $oldVenvMoved = $true
    }
    Move-Item -LiteralPath $stagedVenv -Destination $venvPath
    $venvSwitched = $true
    & (Join-Path $venvPath 'Scripts\python.exe') -m pip install --quiet --no-index --require-hashes --force-reinstall --find-links (Join-Path $pluginRoot 'runtime\wheels') -r (Join-Path $pluginRoot 'requirements.lock') *> $null
    if ($LASTEXITCODE -ne 0) { throw 'final_cli_launcher_regeneration_failed' }
    & (Join-Path $venvPath 'Scripts\skilltree.exe') --help *> $null
    if ($LASTEXITCODE -ne 0) { throw 'final_cli_launcher_smoke_failed' }
    Write-CliShim $dataDir
    Register-UserCliPath $dataDir
    Register-UserRuntimeData $dataDir
    $state = @{ schema_version='skilltree-runtime/v1'; plugin_root=$pluginRoot; plugin_version=$manifest.plugin.version; core_version=$manifest.core.version; skilltree_schema_version=$manifest.schema.version; bundle_hash=$manifest.bundle_hash; hook_bundle_hash=$manifest.hook_bundle.hash; installed_at=[DateTime]::UtcNow.ToString('o') } | ConvertTo-Json -Compress
    $tempState = $statePath + '.tmp'
    [IO.File]::WriteAllText($tempState, $state, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tempState -Destination $statePath -Force
    Write-SetupResult 'installed' 0
} catch {
    if ($oldVenvMoved -and $rollbackVenv -and (Test-Path -LiteralPath $rollbackVenv)) {
        $venvPath = Join-Path $dataDir 'venv'
        if (Test-Path -LiteralPath $venvPath) { Remove-Item -LiteralPath $venvPath -Recurse -Force }
        Move-Item -LiteralPath $rollbackVenv -Destination $venvPath
    }
    if ($databaseCreatedByThisInstall) {
        foreach ($databaseArtifact in @($databasePath, ($databasePath + '-wal'), ($databasePath + '-shm'))) {
            if (Test-Path -LiteralPath $databaseArtifact) { Remove-Item -LiteralPath $databaseArtifact -Force }
        }
    }
    Write-SetupResult 'rolled_back' 7 'runtime_switch_failed'
}
