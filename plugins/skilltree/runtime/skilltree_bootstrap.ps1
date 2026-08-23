param()

$ErrorActionPreference = 'Stop'

function Write-HookResult([string]$Reason) {
    @{ decision = 'block'; reason = $Reason } | ConvertTo-Json -Compress
    exit 0
}

function Test-AbsoluteWindowsPath([string]$Path) {
    return $Path -match '^[A-Za-z]:\\[^\r\n"]*$'
}

function Test-PythonExecutablePath([string]$Path) {
    return (Test-AbsoluteWindowsPath $Path) -and $Path.EndsWith('python.exe', [StringComparison]::OrdinalIgnoreCase)
}

function Test-InputJsonPath([string]$Path) {
    if (-not (Test-AbsoluteWindowsPath $Path)) { return $false }
    try {
        return [IO.File]::Exists($Path) -and -not [IO.Directory]::Exists($Path)
    } catch {
        return $false
    }
}

function Get-ConfigFailureCode($Payload) {
    if ($null -ne $Payload -and $null -ne $Payload.error -and $Payload.error.code -is [string]) {
        $code = [string]$Payload.error.code
        if ($code -in @('invalid_schema', 'conflict', 'authorization_required')) {
            return $code
        }
    }
    return 'internal_error'
}

function Invoke-ConfigCommand([string]$Command, [string]$InputPath, [string]$DataDir, [string]$RuntimePython) {
    try {
        if (-not (Test-InputJsonPath $InputPath)) {
            Write-HookResult 'skilltree_config_failed:invalid_schema'
        }
        if (-not (Test-AbsoluteWindowsPath $RuntimePython) -or -not [IO.File]::Exists($RuntimePython)) {
            Write-HookResult 'skilltree_config_failed:internal_error'
        }

        $arguments = @(
            '-I', '-m', 'skilltree', 'config', $Command,
            '--data-dir', $DataDir, '--input', $InputPath
        )
        $cliOutput = & $RuntimePython @arguments 2>$null
        $cliExit = $LASTEXITCODE
        $serialized = ($cliOutput -join [Environment]::NewLine).Trim()
        if ([string]::IsNullOrWhiteSpace($serialized)) {
            Write-HookResult 'skilltree_config_failed:internal_error'
        }
        $payload = $serialized | ConvertFrom-Json
        if ($cliExit -ne 0 -or $null -eq $payload -or $payload.ok -ne $true) {
            Write-HookResult ('skilltree_config_failed:' + (Get-ConfigFailureCode $payload))
        }
        if ($Command -eq 'status') {
            $data = $payload.data
            $keys = @('trace_capture_enabled', 'memory_read_enabled', 'memory_write_enabled', 'replay_capture_enabled')
            if ($null -eq $data -or $data.config_version -isnot [int] -or [int]$data.config_version -lt 1) {
                Write-HookResult 'skilltree_config_failed:internal_error'
            }
            $consents = $data.consents
            if ($null -eq $consents) {
                Write-HookResult 'skilltree_config_failed:internal_error'
            }
            foreach ($key in $keys) {
                if ($null -eq $consents.PSObject.Properties[$key] -or $consents.$key -isnot [bool]) {
                    Write-HookResult 'skilltree_config_failed:internal_error'
                }
            }
            $reason = 'skilltree_config_status/v1;config_version=' + [int]$data.config_version
            foreach ($key in $keys) {
                $reason += ';' + $key + '=' + ([string]$consents.$key).ToLowerInvariant()
            }
            if ([Text.Encoding]::ASCII.GetByteCount($reason) -gt 256) {
                Write-HookResult 'skilltree_config_failed:internal_error'
            }
            Write-HookResult $reason
        }

        $data = $payload.data
        $keys = @('trace_capture_enabled', 'memory_read_enabled', 'memory_write_enabled', 'replay_capture_enabled')
        if ($null -eq $data -or $data.config_version -isnot [int] -or [int]$data.config_version -lt 1) {
            Write-HookResult 'skilltree_config_failed:internal_error'
        }
        $changed = @($data.changed_keys)
        if ($null -eq $data.PSObject.Properties['changed_keys'] -or
            ($changed | Where-Object { $_ -isnot [string] -or $_ -notin $keys }).Count -gt 0 -or
            (($changed | Select-Object -Unique).Count -ne $changed.Count)) {
            Write-HookResult 'skilltree_config_failed:internal_error'
        }
        $ordered = @($keys | Where-Object { $_ -in $changed })
        $changedText = if ($ordered.Count -eq 0) { 'empty' } else { $ordered -join ',' }
        $reason = 'skilltree_config_updated/v1;config_version=' + [int]$data.config_version + ';changed_keys=' + $changedText
        if ([Text.Encoding]::ASCII.GetByteCount($reason) -gt 256) {
            Write-HookResult 'skilltree_config_failed:internal_error'
        }
        Write-HookResult $reason
    } catch {
        Write-HookResult 'skilltree_config_failed:internal_error'
    }
}

try {
    $inputLine = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($inputLine)) { exit 0 }
    $hookInput = $inputLine | ConvertFrom-Json
    if ($null -eq $hookInput.prompt -or $hookInput.prompt -isnot [string]) { exit 0 }
    $prompt = [string]$hookInput.prompt
    if (-not $prompt.StartsWith('$skilltree-bootstrap', [StringComparison]::Ordinal)) { exit 0 }

    $configMatch = [regex]::Match($prompt, '^\$skilltree-bootstrap config (status|set-consent) --input "([^"]+)"$')
    $isConfigCommand = $configMatch.Success
    $hasConfigPrefix = $prompt.StartsWith('$skilltree-bootstrap config ', [StringComparison]::Ordinal)
    if (-not $env:PLUGIN_ROOT -or -not $env:PLUGIN_DATA) {
        exit 0
    }

    if ($hasConfigPrefix -and -not $isConfigCommand) {
        Write-HookResult 'skilltree_config_failed:invalid_schema'
    }

    $match = [regex]::Match($prompt, '^\$skilltree-bootstrap install --python "([^"]+)"$')
    if ($isConfigCommand) {
        $handlerRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
        $pluginRoot = [IO.Path]::GetFullPath($env:PLUGIN_ROOT)
        if (-not $pluginRoot.Equals($handlerRoot, [StringComparison]::OrdinalIgnoreCase)) {
            Write-HookResult 'skilltree_config_failed:internal_error'
        }
        $dataDir = [IO.Path]::GetFullPath($env:PLUGIN_DATA)
        if (-not (Test-AbsoluteWindowsPath $dataDir) -or $dataDir.StartsWith($pluginRoot, [StringComparison]::OrdinalIgnoreCase) -or $pluginRoot.StartsWith($dataDir, [StringComparison]::OrdinalIgnoreCase)) {
            Write-HookResult 'skilltree_config_failed:internal_error'
        }
        $runtimePython = Join-Path $dataDir 'venv\Scripts\python.exe'
        Invoke-ConfigCommand $configMatch.Groups[1].Value $configMatch.Groups[2].Value $dataDir $runtimePython
    }
    if (-not $match.Success -or -not (Test-PythonExecutablePath $match.Groups[1].Value)) {
        Write-HookResult 'skilltree_bootstrap_failed:invalid_bootstrap_request'
    }

    $handlerRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $pluginRoot = [IO.Path]::GetFullPath($env:PLUGIN_ROOT)
    if (-not $pluginRoot.Equals($handlerRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Write-HookResult 'skilltree_bootstrap_failed:invalid_argument'
    }
    $dataDir = [IO.Path]::GetFullPath($env:PLUGIN_DATA)
    if (-not (Test-AbsoluteWindowsPath $dataDir) -or $dataDir.StartsWith($pluginRoot, [StringComparison]::OrdinalIgnoreCase) -or $pluginRoot.StartsWith($dataDir, [StringComparison]::OrdinalIgnoreCase)) {
        Write-HookResult 'skilltree_bootstrap_failed:invalid_argument'
    }

    $setupOutput = & (Join-Path $handlerRoot 'scripts\setup.ps1') -PluginData $dataDir -PythonPath $match.Groups[1].Value
    $setupExit = $LASTEXITCODE
    if ($setupExit -eq 0) {
        $setupResult = $setupOutput | ConvertFrom-Json
        if ($setupResult.status -eq 'already_installed') { Write-HookResult 'skilltree_bootstrap_already_installed' }
        if ($setupResult.status -eq 'installed') { Write-HookResult 'skilltree_bootstrap_installed' }
    }
    $codes = @{ 2 = 'invalid_argument'; 3 = 'bundle_validation_failed'; 4 = 'offline_install_failed'; 5 = 'smoke_check_failed'; 6 = 'database_initialize_failed'; 7 = 'runtime_switch_failed' }
    $code = if ($codes.ContainsKey($setupExit)) { $codes[$setupExit] } else { 'runtime_switch_failed' }
    Write-HookResult ('skilltree_bootstrap_failed:' + $code)
} catch {
    Write-HookResult 'skilltree_bootstrap_failed:runtime_switch_failed'
}
