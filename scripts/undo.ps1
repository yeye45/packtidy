# Undo a run; an undo run can itself be undone. Repeated undo is idempotent.
param(
    [Parameter(Mandatory)][string]$Journal, [string]$RunId,
    [int]$Seq = 0, [switch]$Last, [switch]$List, [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$arguments = @('undo','--journal',$Journal)
$selectionCount = [int][bool]$RunId + [int]($Seq -gt 0) + [int][bool]$Last + [int][bool]$List
if ($selectionCount -ne 1) { throw 'Select exactly one of -RunId, -Seq, -Last or -List.' }
if ($RunId) { $arguments += @('--run-id',$RunId) }
if ($Seq -gt 0) { $arguments += @('--seq',[string]$Seq) }
if ($Last) { $arguments += '--last' }
if ($List) { $arguments += '--list' }
if ($DryRun) { $arguments += '--dry-run' }
Invoke-Organizer -Arguments $arguments
exit $script:OrganizerExitCode
