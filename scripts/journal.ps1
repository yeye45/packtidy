# Append-only eight-column journal helpers. File mutations must use apply/undo.
. (Join-Path $PSScriptRoot 'common.ps1')
$script:JournalHeader = @('seq','run_id','ts','op','before','after','reason','status') -join "`t"
function New-JournalHeader { return $script:JournalHeader }
function Read-Journal {
    param([Parameter(Mandatory)][string]$Path)
    $json = Invoke-Organizer -Arguments @('journal','--path',$Path,'--action','read')
    if ($script:OrganizerExitCode -ne 0) { throw 'Unable to read journal.' }
    return @($json | ConvertFrom-Json)
}
function Add-Journal {
    param(
        [Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$RunId,
        [Parameter(Mandatory)][string]$Op, [string]$Before='-', [string]$After='-',
        [string]$Reason='-', [string]$Status='done'
    )
    $seq = Invoke-Organizer -Arguments @('journal','--path',$Path,'--action','append',
        '--run-id',$RunId,'--op',$Op,('--before='+$Before),('--after='+$After),
        ('--reason='+$Reason),('--status='+$Status))
    if ($script:OrganizerExitCode -ne 0) { throw 'Unable to append journal.' }
    return [int]$seq
}
function Get-JournalRuns {
    param([Parameter(Mandatory)][string]$Path)
    $json = Invoke-Organizer -Arguments @('journal','--path',$Path,'--action','runs')
    if ($script:OrganizerExitCode -ne 0) { throw 'Unable to list journal runs.' }
    return @($json | ConvertFrom-Json)
}
