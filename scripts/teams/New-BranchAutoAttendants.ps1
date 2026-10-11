<#
.SYNOPSIS
  Creates (idempotently) one Teams resource account + Auto Attendant per branch
  from data-tables/branch-directory.csv and assigns the branch's Direct Routing
  number (TeamsDID) to it.

.DESCRIPTION
  For each CSV row:
    1. Resource account  aa-<BranchCode>@<Domain>  (Auto Attendant app id)
    2. Direct Routing phone number = TeamsDID        (requires a Teams Phone Resource Account licence – assign first)
    3. Auto Attendant "AA - <BranchName> Branch"     (greeting + default route to the branch target you set below)
    4. Association resource account -> AA

  Re-running skips objects that already exist. Use -WhatIf to preview.

.PARAMETER CsvPath      Path to branch-directory.csv
.PARAMETER Domain       Tenant SIP domain, e.g. bank.com.au
.PARAMETER ExportSbcPrefixes  Only write the TeamsDID list to teams-dids.txt for the SBC dial plan and exit

.EXAMPLE
  .\New-BranchAutoAttendants.ps1 -CsvPath ..\..\data-tables\branch-directory.csv -Domain bank.com.au -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)] [string] $CsvPath,
    [Parameter(Mandatory)] [string] $Domain,
    [string] $TimeZone = "AUS Eastern Standard Time",
    [string] $Language = "en-AU",
    [switch] $ExportSbcPrefixes
)

$AutoAttendantAppId = "ce933385-9390-45d1-9512-c8d228074e07"

$branches = Import-Csv -Path $CsvPath | Where-Object { $_.TeamsDID -and $_.TeamsDID.Trim() -ne "" }
if (-not $branches) { throw "No rows with a TeamsDID in $CsvPath" }

if ($ExportSbcPrefixes) {
    $out = Join-Path (Split-Path $CsvPath) "teams-dids.txt"
    $branches | ForEach-Object { $_.TeamsDID.Trim() } | Sort-Object -Unique | Set-Content -Path $out
    Write-Host "Wrote $($branches.Count) Teams DIDs to $out – import into the SBC 'route to Teams' prefix list"
    return
}

if (-not (Get-Command Get-CsOnlineApplicationInstance -ErrorAction SilentlyContinue)) {
    Import-Module MicrosoftTeams -ErrorAction Stop
}
try { $null = Get-CsTenant -ErrorAction Stop } catch { Connect-MicrosoftTeams }

$needLicence = @()

foreach ($b in $branches) {
    $code     = $b.BranchCode.Trim()
    $name     = $b.BranchName.Trim()
    $teamsDid = $b.TeamsDID.Trim()
    $upn      = "aa-$($code.ToLower())@$Domain"
    $aaName   = "AA - $name Branch"

    Write-Host "=== $code  $name  $teamsDid ==="

    # 1. Resource account ----------------------------------------------------
    $ra = Get-CsOnlineApplicationInstance -Identity $upn -ErrorAction SilentlyContinue
    if (-not $ra) {
        if ($PSCmdlet.ShouldProcess($upn, "New-CsOnlineApplicationInstance")) {
            $ra = New-CsOnlineApplicationInstance -UserPrincipalName $upn -ApplicationId $AutoAttendantAppId -DisplayName $aaName
            $needLicence += $upn
            Write-Host "  created resource account $upn (assign Teams Phone Resource Account licence, then re-run for number assignment)"
            continue   # number/AA need the licence first
        }
    } else {
        Write-Host "  resource account exists"
    }

    # 2. Phone number --------------------------------------------------------
    $current = (Get-CsOnlineUser -Identity $upn -ErrorAction SilentlyContinue).LineUri -replace '^tel:', ''
    if ($current -ne $teamsDid) {
        if ($PSCmdlet.ShouldProcess($upn, "Set-CsPhoneNumberAssignment $teamsDid (DirectRouting)")) {
            try {
                Set-CsPhoneNumberAssignment -Identity $upn -PhoneNumber $teamsDid -PhoneNumberType DirectRouting -ErrorAction Stop
                Write-Host "  assigned $teamsDid"
            } catch {
                Write-Warning "  could not assign $teamsDid to $upn – licence missing or not propagated yet: $($_.Exception.Message)"
                $needLicence += $upn
                continue
            }
        }
    } else {
        Write-Host "  number already $teamsDid"
    }

    # 3. Auto Attendant ------------------------------------------------------
    $aa = Get-CsAutoAttendant -NameFilter $aaName -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq $aaName }
    if (-not $aa) {
        if ($PSCmdlet.ShouldProcess($aaName, "New-CsAutoAttendant")) {
            # Default target: EDIT THIS per your design (call queue / user / shared voicemail).
            # Placeholder below disconnects after the greeting so the AA is valid on day 1.
            $greeting   = New-CsAutoAttendantPrompt -TextToSpeechPrompt "Thank you for calling the $name branch."
            $disconnect = New-CsAutoAttendantMenuOption -Action DisconnectCall -DtmfResponse Automatic
            $menu       = New-CsAutoAttendantMenu -Name "Default" -MenuOptions @($disconnect)
            $callFlow   = New-CsAutoAttendantCallFlow -Name "Default call flow" -Greetings @($greeting) -Menu $menu
            $aa = New-CsAutoAttendant -Name $aaName -LanguageId $Language -TimeZoneId $TimeZone -DefaultCallFlow $callFlow
            Write-Host "  created $aaName (default target is a placeholder – point it at the branch call queue)"
        }
    } else {
        Write-Host "  auto attendant exists"
    }

    # 4. Association ---------------------------------------------------------
    if ($aa -and $ra) {
        $assoc = Get-CsOnlineApplicationInstanceAssociation -Identity $ra.ObjectId -ErrorAction SilentlyContinue
        if (-not $assoc -or $assoc.ConfigurationId -ne $aa.Identity) {
            if ($PSCmdlet.ShouldProcess($upn, "Associate with $aaName")) {
                New-CsOnlineApplicationInstanceAssociation -Identities @($ra.ObjectId) -ConfigurationId $aa.Identity -ConfigurationType AutoAttendant | Out-Null
                Write-Host "  associated"
            }
        } else {
            Write-Host "  already associated"
        }
    }
}

if ($needLicence) {
    Write-Host ""
    Write-Host "Assign a 'Microsoft Teams Phone Resource Account' licence to these accounts, wait for propagation, then re-run:"
    $needLicence | Sort-Object -Unique | ForEach-Object { Write-Host "  $_" }
}
