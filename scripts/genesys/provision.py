#!/usr/bin/env python3
"""
Provision the Branch Inbound IVR objects in Genesys Cloud from
data-tables/branch-directory.csv.

Sub-commands
  datatable   create the BranchDirectory data table (if missing) and upsert rows
  callroute   create/update the "Branch Inbound" call route (IVR) with every DID
  all         both of the above

Environment
  GENESYS_REGION         e.g. mypurecloud.com.au  (default)
  GENESYS_CLIENT_ID      OAuth client-credentials client id
  GENESYS_CLIENT_SECRET  OAuth client-credentials client secret

Examples
  python scripts/genesys/provision.py datatable
  python scripts/genesys/provision.py datatable --delete-missing
  python scripts/genesys/provision.py callroute --flow-name "Branch Inbound Main"
  python scripts/genesys/provision.py all --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = REPO_ROOT / "data-tables" / "branch-directory.csv"
SCHEMA_PATH = REPO_ROOT / "data-tables" / "branch-directory.schema.json"

DATATABLE_NAME = "BranchDirectory"
CALLROUTE_NAME = "Branch Inbound"
FLOW_NAME = "Branch Inbound Main"

BOOL_COLUMNS = {"BranchTransferEnabled"}


# ----------------------------------------------------------------------------
# API client
# ----------------------------------------------------------------------------
class Genesys:
    def __init__(self, region: str, client_id: str, client_secret: str):
        self.base = f"https://api.{region}"
        resp = requests.post(
            f"https://login.{region}/oauth/token",
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=30,
        )
        resp.raise_for_status()
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        self.session.headers["Content-Type"] = "application/json"

    def _req(self, method: str, path: str, **kw):
        r = self.session.request(method, f"{self.base}{path}", timeout=60, **kw)
        if r.status_code >= 400:
            sys.exit(f"{method} {path} -> {r.status_code}: {r.text}")
        return r.json() if r.text else None

    def get(self, path, **params):
        return self._req("GET", path, params=params)

    def post(self, path, body):
        return self._req("POST", path, data=json.dumps(body))

    def put(self, path, body):
        return self._req("PUT", path, data=json.dumps(body))

    def delete(self, path):
        r = self.session.delete(f"{self.base}{path}", timeout=60)
        if r.status_code >= 400:
            sys.exit(f"DELETE {path} -> {r.status_code}: {r.text}")

    def paged(self, path, **params):
        page = 1
        while True:
            data = self.get(path, pageSize=100, pageNumber=page, **params)
            for e in data.get("entities", []):
                yield e
            if page >= data.get("pageCount", 1):
                break
            page += 1


# ----------------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------------
def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"{path} has no rows")
    seen = set()
    for r in rows:
        did = r["DID"].strip()
        if not did.startswith("+") or not did[1:].isdigit():
            sys.exit(f"DID '{did}' is not E.164 (+61...)")
        if did in seen:
            sys.exit(f"Duplicate DID {did}")
        seen.add(did)
        teams = r.get("TeamsDID", "").strip()
        if teams and (not teams.startswith("+") or not teams[1:].isdigit()):
            sys.exit(f"TeamsDID '{teams}' for {did} is not E.164")
    return rows


def row_to_datatable(r: dict) -> dict:
    out = {"key": r["DID"].strip()}
    for col, val in r.items():
        if col == "DID":
            continue
        val = (val or "").strip()
        if col in BOOL_COLUMNS:
            out[col] = val.lower() in ("true", "1", "yes", "y")
        else:
            out[col] = val
    return out


# ----------------------------------------------------------------------------
# Data table
# ----------------------------------------------------------------------------
def find_datatable(gc: Genesys, name: str):
    for dt in gc.paged("/api/v2/flows/datatables", name=name):
        if dt["name"] == name:
            return dt
    return None


def cmd_datatable(gc: Genesys, rows: list[dict], dry_run: bool, delete_missing: bool):
    dt = find_datatable(gc, DATATABLE_NAME)
    if dt is None:
        schema = json.loads(SCHEMA_PATH.read_text())
        body = {"name": DATATABLE_NAME, "description": "Branch DID -> branch details / Teams DID", "schema": schema}
        print(f"[datatable] creating {DATATABLE_NAME}")
        if dry_run:
            dt = {"id": "DRY-RUN"}
        else:
            dt = gc.post("/api/v2/flows/datatables", body)
    else:
        print(f"[datatable] found {DATATABLE_NAME} ({dt['id']})")

    existing: dict[str, dict] = {}
    if dt["id"] != "DRY-RUN":
        for row in gc.paged(f"/api/v2/flows/datatables/{dt['id']}/rows", showbrief="false"):
            existing[row["key"]] = row

    wanted = {r["DID"].strip(): row_to_datatable(r) for r in rows}
    created = updated = unchanged = deleted = 0

    for key, body in wanted.items():
        cur = existing.get(key)
        if cur is None:
            created += 1
            print(f"  + {key}  {body.get('BranchName')}")
            if not dry_run:
                gc.post(f"/api/v2/flows/datatables/{dt['id']}/rows", body)
        else:
            diff = {k: v for k, v in body.items() if cur.get(k) != v}
            if diff:
                updated += 1
                print(f"  ~ {key}  {diff}")
                if not dry_run:
                    gc.put(f"/api/v2/flows/datatables/{dt['id']}/rows/{key}", body)
            else:
                unchanged += 1

    if delete_missing:
        for key in existing.keys() - wanted.keys():
            deleted += 1
            print(f"  - {key}")
            if not dry_run:
                gc.delete(f"/api/v2/flows/datatables/{dt['id']}/rows/{key}")

    print(f"[datatable] created={created} updated={updated} unchanged={unchanged} deleted={deleted}")


# ----------------------------------------------------------------------------
# Call route (IVR)
# ----------------------------------------------------------------------------
def find_flow(gc: Genesys, name: str):
    for fl in gc.paged("/api/v2/flows", name=name, type="inboundcall"):
        if fl["name"] == name:
            return fl
    return None


def find_ivr(gc: Genesys, name: str):
    for ivr in gc.paged("/api/v2/architect/ivrs", name=name):
        if ivr["name"] == name:
            return ivr
    return None


def cmd_callroute(gc: Genesys, rows: list[dict], dry_run: bool, flow_name: str):
    flow = find_flow(gc, flow_name)
    if flow is None:
        sys.exit(f"[callroute] inbound call flow '{flow_name}' not found – publish it first (archy publish)")
    dnis = sorted(r["DID"].strip() for r in rows)

    ivr = find_ivr(gc, CALLROUTE_NAME)
    if ivr is None:
        body = {"name": CALLROUTE_NAME, "dnis": dnis, "openHoursFlow": {"id": flow["id"]}}
        print(f"[callroute] creating '{CALLROUTE_NAME}' with {len(dnis)} DIDs -> flow {flow_name}")
        if not dry_run:
            gc.post("/api/v2/architect/ivrs", body)
        return

    current = sorted(ivr.get("dnis", []))
    add = sorted(set(dnis) - set(current))
    remove = sorted(set(current) - set(dnis))
    flow_changed = (ivr.get("openHoursFlow") or {}).get("id") != flow["id"]
    if not add and not remove and not flow_changed:
        print(f"[callroute] '{CALLROUTE_NAME}' already up to date ({len(dnis)} DIDs)")
        return
    for d in add:
        print(f"  + {d}")
    for d in remove:
        print(f"  - {d}")
    if flow_changed:
        print(f"  ~ openHoursFlow -> {flow_name}")
    ivr["dnis"] = dnis
    ivr["openHoursFlow"] = {"id": flow["id"]}
    if not dry_run:
        gc.put(f"/api/v2/architect/ivrs/{ivr['id']}", ivr)
    print(f"[callroute] updated '{CALLROUTE_NAME}'")


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["datatable", "callroute", "all"])
    ap.add_argument("--csv", default=str(CSV_PATH))
    ap.add_argument("--flow-name", default=FLOW_NAME)
    ap.add_argument("--delete-missing", action="store_true", help="delete data-table rows not present in the CSV")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    region = os.environ.get("GENESYS_REGION", "mypurecloud.com.au")
    cid = os.environ.get("GENESYS_CLIENT_ID")
    secret = os.environ.get("GENESYS_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Set GENESYS_CLIENT_ID and GENESYS_CLIENT_SECRET")

    rows = load_rows(Path(args.csv))
    print(f"Loaded {len(rows)} branches from {args.csv}")
    gc = Genesys(region, cid, secret)

    if args.command in ("datatable", "all"):
        cmd_datatable(gc, rows, args.dry_run, args.delete_missing)
    if args.command in ("callroute", "all"):
        cmd_callroute(gc, rows, args.dry_run, args.flow_name)


if __name__ == "__main__":
    main()
