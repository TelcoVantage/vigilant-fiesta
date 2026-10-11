# 03 – Microsoft Teams Side (Direct Routing)

Goal: when Genesys dials `+61881233650`, the call lands on the **Perth Branch
Auto Attendant** in Teams.

## 1. Topology

```
Genesys BYOC trunk ──SIP/TLS──► SBC ──SIP/TLS──► sip.pstnhub.microsoft.com (Teams DR)
                                 │
                                 └──────────────► Carrier (PSTN) for everything else
```

The SBC owns the decision "is this called number a Teams number?". Keep that
list in the SBC dial plan in sync with the `TeamsDID` column of the CSV
(most SBCs can import a prefix list; `scripts/teams/New-BranchAutoAttendants.ps1 -ExportSbcPrefixes` writes one).

### SBC rules (vendor-neutral)

| From | Called number | Action |
|------|---------------|--------|
| Genesys trunk | `TeamsDID` list | Route to Teams DR trunk; keep From (customer CLI); optionally set display name `via Genesys` |
| Genesys trunk | anything else | Route to carrier |
| Carrier | the 60 branch DIDs | Route to Genesys trunk (this is how the inbound calls get to Genesys) |
| Teams | anything | Route to carrier (normal outbound) |

If Genesys REFERs (Release Link Transfer), the SBC must accept REFER from the
Genesys trunk and bridge the carrier leg to the Teams leg itself.

## 2. Teams tenant configuration (one-off)

```powershell
Connect-MicrosoftTeams

# SBC as a PSTN gateway (once)
New-CsOnlinePSTNGateway -Fqdn sbc.bank.com.au -SipSignalingPort 5061 -MaxConcurrentSessions 200 -Enabled $true

# PSTN usage + voice route + routing policy (standard DR setup)
Set-CsOnlinePstnUsage -Identity Global -Usage @{Add="AU-All"}
New-CsOnlineVoiceRoute -Identity "AU-All" -NumberPattern ".*" -OnlinePstnGatewayList sbc.bank.com.au -OnlinePstnUsages "AU-All"
New-CsOnlineVoiceRoutingPolicy -Identity "AU-DR" -OnlinePstnUsages "AU-All"
```

## 3. Per-branch objects (bulk from the CSV)

Each branch needs:

1. **Resource account** `aa-<branchcode>@bank.com.au` (application id
   `ce933385-9390-45d1-9512-c8d228074e07` = Auto Attendant).
2. **Teams Phone Resource Account licence** (assign in M365 admin centre or
   via Graph – the script prints the UPNs that still need one).
3. **Direct Routing phone number** = `TeamsDID` from the CSV (rows with a blank `TeamsDID` are skipped).
4. **Auto Attendant** `AA – <BranchName> Branch` with a default call flow that
   greets and routes to the branch's Call Queue (or a person / shared voicemail –
   business decision), associated to the resource account.

```powershell
# dry run
.\scripts\teams\New-BranchAutoAttendants.ps1 -CsvPath ..\..\data-tables\branch-directory.csv -Domain bank.com.au -WhatIf
# real run
.\scripts\teams\New-BranchAutoAttendants.ps1 -CsvPath ..\..\data-tables\branch-directory.csv -Domain bank.com.au
```

The script is idempotent: existing resource accounts / AAs are skipped, phone
numbers are (re)assigned only if different.

## 4. Where does the AA send the call?

Out of scope for the Genesys build but you need an answer per branch. Typical:

| AA menu | Target |
|---------|--------|
| Default (no menu) | Call Queue `CQ – Perth Branch` → ring branch Teams users |
| "Press 0 for the contact centre" | Dial a **dedicated** contact-centre DID that is *not* in `BranchDirectory` (avoids a Genesys→Teams→Genesys loop) |
| After hours | Voicemail to a shared mailbox, or redirect to the contact centre |

## 5. Gotchas

* A number assigned with `-PhoneNumberType DirectRouting` must be routable by
  the SBC → Teams; Teams does not validate it. A typo in `TeamsDID` gives a
  silent fast-busy on option 3 – the test plan covers this.
* Resource accounts need the licence **before** `Set-CsPhoneNumberAssignment`
  succeeds. Allow up to 24 h for licence propagation on brand-new accounts.
* CLI: Teams shows the `From` header the SBC sends. If the Genesys trunk
  overrides calling address, every branch call looks like it comes from the
  trunk number – fix on the Genesys trunk (runbook §3), not in Teams.
