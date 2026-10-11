# 04 – Testing & Operations

## 1. Test plan

| # | Test | Expected |
|---|------|----------|
| 1 | Dial a branch DID (e.g. Perth 02 8123 3650) | Greeting says "Thank you for calling Perth"; menu plays |
| 2 | Press 1 | Lands in `ROS`; agent sees `BranchName=Perth`, `BranchCode=PER` |
| 3 | Press 2 | Lands in `CB`; same participant data |
| 4 | Press 3 | "Transferring you to the Perth branch" → Teams AA for Perth answers; Teams shows **customer's** CLI |
| 5 | Press 3, SBC blocks the Teams route (simulate outage) | Apology → `ROS` |
| 6 | Set Perth `BranchTransferEnabled=false`, press 3 | "Branch not available" → `ROS`; no Teams attempt |
| 7 | Dial a DID that is on the call route but **not** in the table | Generic greeting; 1 & 2 work; 3 → `ROS`; `BranchFound=false` on the interaction |
| 8 | No input ×3 | Default choice → `ROS` |
| 9 | Press 7 (invalid) ×3 | "That is not a valid option" → repeat → default → `ROS` |
| 10 | Repeat 1–4 for 3 more branches in different states (02/03/07/08 DIDs) | As above – proves there is no per-branch logic |
| 11 | Check Performance → Interactions filtered by attribute `BranchName` | Rows for every test call |
| 12 | Genesys trunk utilisation during test 4 with Release Link Transfer on | Genesys leg ends after transfer completes |

## 2. Adding a branch

1. Add the row to `data-tables/branch-directory.csv`.
2. `python scripts/genesys/provision.py datatable` (adds the row).
3. Add the DID to the DID pool, then `python scripts/genesys/provision.py callroute`
   (adds the DNIS to the call route).
4. Teams: `New-BranchAutoAttendants.ps1` (creates the AA) + licence.
5. SBC: add the Teams DID to the Teams prefix list.
6. Run tests 1–4 for that branch.

No flow publish required.

## 3. Removing / pausing a branch

* **Pause option 3 only**: set `BranchTransferEnabled=false` in the data table
  (UI: Admin → Architect → Data Tables → BranchDirectory). Immediate.
* **Remove**: delete the CSV row, re-run `provision.py datatable --delete-missing`
  and `provision.py callroute` (removes the DNIS), release the DID.

## 4. Changing a prompt or adding a menu option

Edit `architect/Branch-Inbound-Main.yaml`, `archy validate`, `archy publish`.
Because there is one flow, this is one publish for all 60 branches.

## 5. Troubleshooting

| Symptom | Likely cause | Check |
|---------|--------------|-------|
| Every caller gets the generic greeting | Trunk presents the DID in a format that doesn't match the key (`0281233650`, `61281233650`, `tel:+61…;user=phone`) | Call → Interaction details → *Called* value; align SBC digit manipulation or extend the normalisation in the `Initialise` task |
| Option 3 gives fast busy | SBC not routing `TeamsDID` to Teams, resource account unlicensed, or number not assigned | SBC SIP trace; `Get-CsOnlineApplicationInstance`; `Get-CsPhoneNumberAssignment -TelephoneNumber +618…` |
| Option 3 connects but Teams sees the trunk number as caller | Genesys trunk overriding calling address | Runbook §3 |
| Agents don't see branch name | Script not bound to participant data | Script designer → variable → *Participant data* `BranchName` |
| Option 3 works but Genesys trunk channels stay busy for the whole call | Release Link Transfer off, or SBC rejecting REFER | Trunk setting + SBC REFER support |

## 6. Monitoring

* Alert on `Transfer to Number` failures: Performance → Flows → *Branch Inbound Main* → outcome `TeamsTransfer` (add a Flow Outcome in the UI if you want
  this as a first-class metric).
* Alert on `BranchFound=false` count > 0 per day – means a DID was pointed at
  the flow before its table row was created.
