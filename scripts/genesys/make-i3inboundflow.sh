#!/usr/bin/env bash
# Produce architect/Branch-Inbound-Main.i3InboundFlow – the native Architect
# file you can import through the portal (Architect → flow → Save ▾ → Import).
#
# Why a script: .i3InboundFlow is Base64+URL-encoded Architect-internal JSON
# with org-specific IDs and a manifest. It cannot be hand-authored from YAML;
# Archy must publish the YAML into an org once, then export it in "architect"
# (native) format.
#
# Prereqs: archy installed, queues ROS/CB and data table BranchDirectory exist
#          in the org (archy validates references), OAuth client creds.
#
#   export GENESYS_REGION=mypurecloud.com.au
#   export GENESYS_CLIENT_ID=...  GENESYS_CLIENT_SECRET=...
#   scripts/genesys/make-i3inboundflow.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
YAML="$REPO_ROOT/architect/Branch-Inbound-Main.yaml"
OUT_DIR="$REPO_ROOT/architect"
FLOW_NAME="Branch Inbound Main"
: "${GENESYS_REGION:=mypurecloud.com.au}"
: "${GENESYS_CLIENT_ID:?set GENESYS_CLIENT_ID}"
: "${GENESYS_CLIENT_SECRET:?set GENESYS_CLIENT_SECRET}"

command -v archy >/dev/null || { echo "archy not found – install from https://developer.genesys.cloud/devapps/archy/"; exit 1; }

echo "1/3 validate YAML"
archy validate --file "$YAML"

echo "2/3 publish YAML to org ($GENESYS_REGION) – creates or updates '$FLOW_NAME'"
archy publish --file "$YAML" \
  --clientId "$GENESYS_CLIENT_ID" --clientSecret "$GENESYS_CLIENT_SECRET" \
  --location "$GENESYS_REGION" --forceUnlock --recreate

echo "3/3 export native Architect file"
archy export --flowName "$FLOW_NAME" --flowType inboundcall --exportType architect \
  --clientId "$GENESYS_CLIENT_ID" --clientSecret "$GENESYS_CLIENT_SECRET" \
  --location "$GENESYS_REGION" --outputDir "$OUT_DIR" --force

ls -1 "$OUT_DIR"/*.i3InboundFlow
echo "Done. Import that file via Architect → (new inbound call flow) → Save ▾ → Import."
