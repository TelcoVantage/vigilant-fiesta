# 02 – Genesys Cloud Build Runbook

Region assumed: AU (`mypurecloud.com.au`, API `https://api.mypurecloud.com.au`).
Adjust `--location` / `GENESYS_REGION` if different.

## Prerequisites

| Item | Where |
|------|-------|
| OAuth client (Client Credentials) with roles: Architect, Routing, Telephony Admin | Admin → Integrations → OAuth |
| Archy CLI installed (`npm i -g archy` or download from developer.genesys.cloud) | Workstation |
| Python 3.10+ with `requests` | Workstation |
| BYOC trunk to the SBC up and registered, 60 DIDs in a DID pool | Admin → Telephony |
| `data-tables/branch-directory.csv` fully populated | This repo |

```bash
export GENESYS_REGION=mypurecloud.com.au
export GENESYS_CLIENT_ID=xxxxxxxx
export GENESYS_CLIENT_SECRET=xxxxxxxx
pip install requests
```

## Step 1 – Queues

Admin → Contact Center → Queues → **Add**

| Queue | Media | Notes |
|-------|-------|-------|
| `ROS` | Voice | Members, wrap-up codes, in-queue flow per standard. |
| `CB`  | Voice | As above. |

Names must match `targetQueue.lit.name` in the YAML exactly (case-sensitive).

## Step 2 – Data Table

Option A – script (recommended, idempotent, re-run after every CSV change):

```bash
python scripts/genesys/provision.py datatable
```

Creates `BranchDirectory` if missing using `data-tables/branch-directory.schema.json`,
then upserts every CSV row (insert new keys, update changed rows, optionally
`--delete-missing` to remove rows no longer in the CSV).

Option B – UI: Admin → Architect → Data Tables → Add. Create the columns in
§4 of the solution design (key column = `DID`, type String), then **Import**
the CSV.

## Step 3 – Trunk settings for the Teams transfer

Admin → Telephony → Trunks → *your BYOC trunk*

| Setting | Value | Why |
|---------|-------|-----|
| Outbound → Calling → Address Override Method | `Unassigned` (pass-through) | Teams AA receives the customer's CLI, not the trunk DID |
| Outbound → Calling → Calling Name/Number | leave blank | as above |
| Protocol → Release Link Transfer | Enabled (if SBC supports REFER) | Genesys drops out of the media after transfer |
| Inbound → Digit manipulation | none – SBC must send E.164 `+61…` in the To/Request-URI | Keeps the data-table keys simple |
| Media → DTMF | RFC 2833 | Menu reliability |

Admin → Telephony → Sites → *site* → Outbound Routes: ensure the route that
matches `+618…` (and every other Teams DID prefix you use) selects the SBC
trunk. Simplest: one catch-all outbound route → SBC, and let the SBC decide
Teams vs PSTN.

## Step 4 – Publish the flow

```bash
archy validate --file architect/Branch-Inbound-Main.yaml
archy publish  --file architect/Branch-Inbound-Main.yaml \
               --clientId "$GENESYS_CLIENT_ID" --clientSecret "$GENESYS_CLIENT_SECRET" \
               --location "$GENESYS_REGION" --forceUnlock
```

Then open Admin → Architect → `Branch Inbound Main` and check:

1. Data Table Lookup action references `BranchDirectory` and all 6 output
   columns are mapped.
2. Main Menu → *Settings* → **Default menu choice = Press 1 (ROS)** and
   repeat count = 3 (set in the UI if Archy did not carry it).
3. Replace TTS prompts with recorded prompts if the business supplies them
   (TTS of "ROS" is unreliable – record it, or spell it `R.O.S.` in the TTS).
4. Re-publish from the UI if you changed anything.

## Step 5 – Call Route with the 60 DIDs

```bash
python scripts/genesys/provision.py callroute
```

Creates/updates call route **Branch Inbound** (Admin → Routing → Call Routing)
with every `DID` in the CSV as a DNIS and `Branch Inbound Main` as the
open-hours flow. The DIDs must already exist in a DID pool
(Admin → Telephony → DID Numbers) and not be assigned elsewhere.

UI equivalent: Admin → Routing → Call Routing → Add → name `Branch Inbound`,
select the flow, add each DID under *Inbound Numbers*.

## Step 6 – Agent experience (optional but recommended)

* Admin → Contact Center → Scripts: add a text field bound to participant data
  `BranchName` / `BranchCode` so agents see "Caller dialed: Perth branch".
* Performance → Interactions: add column *Attribute: BranchName* for
  supervisors.

## Step 7 – Smoke test

See `docs/04-testing-and-operations.md` §1. Minimum: call one branch DID, press
1, 2, 3; call a DID that is *not* in the table; set one row's
`BranchTransferEnabled=false` and press 3.
