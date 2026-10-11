# Branch Inbound IVR – Genesys Cloud ↔ Microsoft Teams

Dynamic inbound call flow for a bank with **60 branch DIDs** terminating on a
**single** Genesys Cloud Architect flow. The dialed number identifies the
branch via a Data Table lookup, every caller hears the same three-option IVR,
and option 3 hands the call back to that branch's **Microsoft Teams Auto
Attendant** on its own Teams DID.

```
Caller dials 02 8123 3650 (Perth branch DID, routed to Genesys)
        │
        ▼
Genesys Cloud – Call Route "Branch Inbound" (all 60 DIDs)
        │
        ▼
Architect flow  "Branch Inbound Main"
  1. Normalise dialed number → +61281233650
  2. Data Table lookup  BranchDirectory[+61281233650]
        → BranchCode=PER, BranchName=Perth, TeamsDID=+61881233650
  3. Attach branch details as participant data (screen pop / reporting)
  4. Main Menu (same for every branch)
        1 → ACD queue  ROS
        2 → ACD queue  CB
        3 → Transfer to Number  Flow.TeamsDID  (+61881233650)
                │
                ▼
        Teams Direct Routing SBC → Teams Auto Attendant "Perth Branch"
```

## Repository layout

| Path | Purpose |
|------|---------|
| `docs/01-solution-design.md` | Architecture, design decisions, call-flow logic, edge cases |
| `docs/02-genesys-build-runbook.md` | Step-by-step build in Genesys Cloud (queues, data table, flow, call route, trunk) |
| `docs/03-teams-direct-routing.md` | Teams side: SBC, voice routes, resource accounts, Auto Attendants |
| `docs/04-testing-and-operations.md` | Test plan, adding/removing a branch, troubleshooting |
| `architect/Branch-Inbound-Main.yaml` | Archy definition of the inbound call flow (publish with `archy publish`) |
| `scripts/genesys/make-i3inboundflow.sh` | Publishes the YAML once and exports `architect/Branch-Inbound-Main.i3InboundFlow` for portal import |
| `scripts/genesys/decode-i3flow.py` | Decode/encode an `.i3InboundFlow` to JSON for inspection or diffing |
| `data-tables/branch-directory.csv` | **Single source of truth** – one row per branch DID (populate all 60) |
| `data-tables/branch-directory.schema.json` | JSON schema for the Genesys Data Table |
| `scripts/genesys/provision.py` | Creates/updates the Data Table rows and the Call Route from the CSV |
| `scripts/teams/New-BranchAutoAttendants.ps1` | Creates Teams resource accounts + Auto Attendants from the same CSV |

## Quick start

1. Fill in `data-tables/branch-directory.csv` with all 60 branches (E.164 numbers).
2. Create the two ACD queues `ROS` and `CB` in Genesys Cloud.
3. Provision the data table: `python scripts/genesys/provision.py datatable`.
4. Publish the flow: `archy publish --file architect/Branch-Inbound-Main.yaml --clientId … --clientSecret … --location mypurecloud.com.au`.
   Need a portal-importable `.i3InboundFlow` instead? Run `scripts/genesys/make-i3inboundflow.sh` (see note below).
5. Create the call route with all 60 DIDs: `python scripts/genesys/provision.py callroute`.
6. Build the Teams side (`docs/03-teams-direct-routing.md`, `scripts/teams/…`).
7. Run the test plan in `docs/04-testing-and-operations.md`.

## About `.i3InboundFlow` files

The portal's Import button (Architect → flow → Save ▾ → Import) takes
Architect's **native** export: a Base64 + URL-encoded, undocumented JSON
document with org-specific object IDs and a manifest. It is not a renamed
YAML and Genesys does not support authoring or editing it by hand. The
supported way to get one from this repo's YAML is:

```bash
export GENESYS_REGION=mypurecloud.com.au GENESYS_CLIENT_ID=… GENESYS_CLIENT_SECRET=…
scripts/genesys/make-i3inboundflow.sh      # archy validate → publish → export --exportType architect
```

That writes `architect/Branch-Inbound-Main.i3InboundFlow`, which you (or
another org) can import through the portal. Archy must be able to resolve
the `ROS` and `CB` queues and the `BranchDirectory` data table in the org it
publishes to, so build those first (steps 2 and 3 above).

> Flow YAML, schema and scripts are written against the Genesys Cloud public
> API / Archy as at 2026. Always run `archy validate` before `archy publish`
> and review the flow in the Architect UI after the first publish.
