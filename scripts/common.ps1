# Shared Python bridge; UTF-8 BOM keeps Windows PowerShell 5.1 compatible.
$script:OrganizerEngine = Join-Path (Split-Path -Parent $PSScriptRoot) 'core/executor.py'
function Invoke-Organizer {
    param([string[]]$Arguments)
    $oldEncoding = $env:PYTHONIOENCODING
    $oldConsoleEncoding = [Console]::OutputEncoding
    try {
        $env:PYTHONIOENCODING = 'utf-8'
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        & python -B $script:OrganizerEngine @Arguments
        $script:OrganizerExitCode = $LASTEXITCODE
    } finally {
        $env:PYTHONIOENCODING = $oldEncoding
        [Console]::OutputEncoding = $oldConsoleEncoding
    }
}
