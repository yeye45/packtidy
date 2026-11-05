# Reconcile interrupted operations using durable transaction records.
param([Parameter(Mandatory)][string]$Root)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Invoke-Organizer -Arguments @('recover','--root',$Root)
exit $script:OrganizerExitCode
