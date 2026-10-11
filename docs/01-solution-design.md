# 01 – Solution Design

## 1. Objective

One Architect inbound call flow serves 60 branch DIDs. The dialed number tells
us which branch the customer "called", every caller gets the same 3-option
IVR, and option 3 returns the call to the originating branch inside Microsoft
Teams.

| Option | Announcement | Target |
|--------|--------------|--------|
| 1 | "For ROS, press 1" | ACD queue **ROS** |
| 2 | "For Country Banking, press 2" | ACD queue **CB** |
| 3 | "To speak to the branch, press 3" | Teams Auto Attendant for the branch (its Teams DID) |

Example: customer dials **02 8123 3650** (Perth branch's public number, routed
to Genesys). Press 3 → Genesys dials **08 8123 3650** → Teams Direct Routing →
Teams Auto Attendant "Perth Branch".

## 2. Design principles

1. **One flow, zero per-branch logic.** Nothing in the flow is branch-specific.
   Adding branch 61 is a data-table row and a DID on the call route, not a flow
   change / re-publish.
2. **Data Table is the single source of truth.** `BranchDirectory` keyed on the
   Genesys-facing DID in E.164. The same CSV feeds the Teams build script so the
   two platforms cannot drift.
3. **Fail safe, never fail silent.** If the DID is not in the table, or the
   Teams transfer fails, the customer still lands in a human queue (ROS by
   default – confirm with the business) and the agent sees why.
4. **Agents get context.** Branch name/code and the dialed DID are attached as
   participant data so they appear in the interaction, the script, and
   reporting (Performance → Interactions can filter on attributes).
   Conversation Schemas were assessed and deferred – see
   `docs/05-conversation-schemas-assessment.md`.

## 3. Components

### 3.1 Genesys Cloud

| Component | Name | Notes |
|-----------|------|-------|
| Call Route (IVR) | `Branch Inbound` | All 60 DIDs attached; Open-hours flow = `Branch Inbound Main`. No schedule (flow handles everything). |
| Inbound Call Flow | `Branch Inbound Main` | `architect/Branch-Inbound-Main.yaml` |
| Data Table | `BranchDirectory` | Key = DID (E.164). Columns in §4. |
| ACD Queues | `ROS`, `CB` | Standard queues; in-queue flow / hold music per standard. |
| Trunk | BYOC Cloud (or Premises) to the bank's SBC | Carries the 60 inbound DIDs **and** the outbound "transfer to Teams" calls. |
| Outbound route / number plan | Default site outbound route → SBC trunk | So `Transfer to Number +618…` leaves via the SBC, not a Genesys-provided PSTN trunk. |

### 3.2 Microsoft Teams

| Component | Notes |
|-----------|-------|
| Direct Routing SBC (AudioCodes / Ribbon / Oracle) | Same SBC terminates the Genesys BYOC trunk. Teams branch DIDs are routed to Teams, never out to the PSTN. |
| Resource account per branch (`aa-per@bank.com.au`) | Licensed with *Teams Phone Resource Account*; DR number `+61881233650`. |
| Auto Attendant per branch (`AA – Perth Branch`) | Greeting, business hours, menu to branch Call Queue / staff. |

## 4. Data Table – `BranchDirectory`

| Column | Type | Key | Example | Purpose |
|--------|------|-----|---------|---------|
| `key` (DID) | string | **yes** | `+61281233650` | Number the customer dialed, as presented by the trunk, E.164 |
| `BranchCode` | string | | `PER` | Short code for reporting / screen pop |
| `BranchName` | string | | `Perth` | Spoken in prompts, shown to agents |
| `State` | string | | `WA` | Optional routing / reporting dimension |
| `TeamsDID` | string | | `+61881233650` | Number Genesys dials for option 3 |
| `BranchTransferEnabled` | boolean | | `true` | Kill-switch per branch (e.g. branch closed, Teams outage) |
| `Notes` | string | | | Free text for admins |

Why key on the DID and not the branch code: the flow has exactly one fact at
runtime – `Call.CalledAddressOriginal` – so the lookup must be by that value.

## 5. Flow logic (what the YAML implements)

```
Start ─► Task: Initialise
          ├─ Update Data   Flow.DialedNumber = normalise(Call.CalledAddressOriginal)
          ├─ Data Table Lookup  BranchDirectory[Flow.DialedNumber]
          │     Found     → Flow.BranchFound=true,  Flow.BranchName/Code/State/TeamsDID/TransferEnabled
          │     Not Found → Flow.BranchFound=false, Flow.BranchName="our bank"
          │     Failure   → same as Not Found
          ├─ Set Participant Data  BranchName, BranchCode, BranchState, DialedDID, BranchFound
          └─ Jump to Menu  Main Menu

Menu: Main Menu  (repeat ×3 on no input / invalid; then default choice → ROS)
   Greeting: "Thank you for calling {BranchName}." (generic if not found)
   1 ─► Transfer to ACD  ROS   (failure → apology → disconnect)
   2 ─► Transfer to ACD  CB    (failure → apology → disconnect)
   3 ─► Jump to Task  Transfer to Branch

Task: Transfer to Branch
   Decision  BranchFound AND BranchTransferEnabled AND TeamsDID not empty
      Yes → Play "Transferring you to the {BranchName} branch"
            Transfer to Number  Flow.TeamsDID
              failure → Play "Sorry, we couldn't connect you to the branch"
                        Transfer to ACD  ROS   (fallback – confirm with business)
      No  → Play "The branch is not available right now, connecting you to our team"
            Transfer to ACD  ROS
```

### 5.1 Number normalisation

`Call.CalledAddressOriginal` arrives as `tel:+61281233650` on a correctly
configured BYOC trunk. The flow strips `tel:` and, defensively, converts a
national-format `02…` into `+612…`. **Configure the SBC/trunk to send E.164**
so the second rule is never needed – it is a safety net, not the plan.

```
Flow.DialedNumber = Replace(Call.CalledAddressOriginal, "tel:", "")
If Left(Flow.DialedNumber, 1) == "0" → "+61" + Substring(Flow.DialedNumber, 1)
```

### 5.2 Transfer to Teams – how the call leaves Genesys

**Recommended: SBC tie-line, same SBC as Teams Direct Routing.**

* Genesys `Transfer to Number +61881233650` → BYOC trunk → SBC.
* SBC has a dial-plan rule: "called number ∈ Teams DR number range → route to
  Teams (sip.pstnhub.microsoft.com)". The call never touches the PSTN, so no
  per-minute carrier cost, and the Teams AA receives the **customer's** CLI
  (set trunk *Calling Address Override Method* = `Unassigned` / pass-through
  and tick *Caller ID / ANI pass-through* on the BYOC trunk).
* Enable **Release Link Transfer (SIP REFER)** on the BYOC trunk if the SBC
  supports it. Genesys then drops out of the media path after the transfer and
  the Genesys call leg ends – otherwise Genesys bridges two legs for the whole
  branch conversation (two trunk channels, two Genesys "minutes").

Alternative: let the transfer go out to the PSTN and in again via the Teams
calling plan / Operator Connect number. Works with zero SBC config, but costs
outbound minutes, keeps Genesys in the media path, and CLI presentation depends
on the carrier. Fine for a pilot; not for 60 branches in production.

### 5.3 What the Teams side sees

The Teams AA is reached by *its* DID, so it does not know (or need to know)
that the call came via Genesys. If the branch wants to see "via Genesys" in
Teams, the SBC can prefix the display name on that route.

## 6. Edge cases and decisions

| Scenario | Behaviour | Decision owner |
|----------|-----------|----------------|
| DID not in table (new DID pointed at flow before row added) | Generic greeting, options 1 & 2 work, option 3 → ROS queue. Participant data `BranchFound=false` so it is reportable. | Built in |
| Teams transfer fails (SBC down, AA not licensed, no answer within timeout) | Apology → ROS queue. | **Confirm fallback queue with business** |
| Branch temporarily closed / Teams outage | Set `BranchTransferEnabled=false` for that row – option 3 goes to ROS with a "branch unavailable" message. No flow change. | Ops |
| Caller presses nothing ×3 | Menu default choice → ROS. | Built in |
| ROS/CB queue transfer fails (queue deleted/renamed) | Apology → disconnect. Covered by monitoring. | Built in |
| After-hours | Not in scope. Options: Schedule Group on the call route, or a `ScheduleGroup` column per branch evaluated in the flow before the menu. | Future |
| Same DID dialed in Teams AA loop (branch AA option "Contact Centre" dials the Genesys DID again) | Harmless – flow re-runs. Avoid loops by having the AA dial a *dedicated* contact-centre DID that is **not** in `BranchDirectory`. | Design note |

## 7. Why not …

* **60 call routes / 60 flows?** Unmaintainable; every prompt change is 60
  publishes.
* **Switch action on `Call.CalledAddress` inside the flow?** Still a flow
  publish per branch change and the branch list becomes invisible to non-
  developers. Data Tables are editable by supervisors with the right role.
* **Transfer to User / Group in Genesys?** The branch staff are Teams users,
  not Genesys users. Transfer to Number (via SBC) is the clean boundary.
* **Genesys Cloud "Microsoft Teams integration"?** It provides presence and
  directory search for agents; it does not route a call into a Teams Auto
  Attendant. Direct Routing via the SBC is still required.
