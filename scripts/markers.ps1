# FOREIGN means no probing/comparison; NOTE never revokes protection.
# SECRET paths are omitted from normal output. Only user declarations can set FOREIGN.
param(
    [Parameter(Mandatory)][string]$Root,
    [ValidateSet('list','add','remove','check','pending','secrets')][string]$Action='list',
    [string]$Path,
    [ValidateSet('FOREIGN','WORKSPACE','NO_TOUCH','ASK','SECRET','NOTE')][string]$Kind,
    [string]$Note='-', [ValidateSet('user','inferred')][string]$By='user'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$arguments = @('markers','--root',$Root,'--action',$Action,'--by',$By,('--note=' + $Note))
if ($Path) { $arguments += @('--path',$Path) }
if ($Kind) { $arguments += @('--kind',$Kind) }
Invoke-Organizer -Arguments $arguments
exit $script:OrganizerExitCode
