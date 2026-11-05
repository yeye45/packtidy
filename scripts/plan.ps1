<#
Generate an approval list from probe results and policy (S4).
Read-only unless the policy is missing; writes plan.tsv only when actions exist.
#>
param(
    [Parameter(Mandatory)][string]$Root,
    [string]$Probe, [string]$Policy, [string]$Out,
    [switch]$DryRun, [switch]$Quiet
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$arguments = @('plan','--root',$Root)
if ($Probe) { $arguments += @('--probe',$Probe) }
if (-not $Policy) {
    $candidate = Join-Path (Split-Path $PSScriptRoot -Parent) 'policy.json'
    if (Test-Path -LiteralPath $candidate) { $Policy = $candidate }
}
if ($Policy) { $arguments += @('--policy',$Policy) }
if ($Out) { $arguments += @('--out',$Out) }
if ($DryRun) { $arguments += '--dry-run' }
if ($Quiet) { $arguments += '--quiet' }
Invoke-Organizer -Arguments $arguments
exit $script:OrganizerExitCode
