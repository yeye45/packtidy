<#
Execute approved actions.tsv via the shared Python engine.
move dst = target directory; rename dst = new leaf name; mkdir dst = absolute directory.
-Plan remains available for rename/merge. split requires explicit actions after grouping.
-DryRun is read-only. Interrupted transactions must be reconciled with recover.ps1.
#>
param(
    [string]$Actions, [string]$Plan, [Parameter(Mandatory)][string]$Root,
    [string]$Reason = '-', [switch]$DryRun,
    [ValidateRange(1,200)][int]$BatchSize = 50,
    [switch]$AllowCrossDrive, [string[]]$Rename
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$arguments = @('apply','--root',$Root,'--batch-size',[string]$BatchSize)
$inputCount = [int][bool]$Actions + [int][bool]$Plan + [int][bool]$Rename
if ($inputCount -ne 1) { throw 'Provide exactly one of -Actions, -Plan or -Rename.' }
if ($Actions) { $arguments += @('--actions',$Actions) }
if ($Plan) { $arguments += @('--plan',$Plan) }
if ($Rename) {
    if ($Rename.Count -ne 2) { throw '-Rename requires source and new name.' }
    $arguments += @('--rename',$Rename[0],$Rename[1],('--reason=' + $Reason))
}
if ($DryRun) { $arguments += '--dry-run' }
if ($AllowCrossDrive) { $arguments += '--allow-cross-drive' }
Invoke-Organizer -Arguments $arguments
exit $script:OrganizerExitCode
