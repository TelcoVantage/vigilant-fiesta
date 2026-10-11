#!/usr/bin/env python3
"""
Generate architect/Branch-Inbound-Main.i3InboundFlow – Architect's native
export format – so the flow can be imported through the Genesys Cloud portal
(Architect → Inbound Call flow → Save ▾ → Import).

The file is Base64( URL-encode( JSON ) ). The JSON structure mirrors real
Architect exports (flowDocumentVersion 1.0). Object IDs for the ROS / CB
queues and the BranchDirectory data table are org-specific:

  * default            : placeholder IDs – after import Architect flags the
                         queue / data-table pickers; re-select them and save.
  * --resolve          : look the real IDs up in your org via the API
                         (GENESYS_REGION / GENESYS_CLIENT_ID / GENESYS_CLIENT_SECRET)
                         so the imported flow validates immediately.

  python scripts/genesys/build_i3inboundflow.py
  python scripts/genesys/build_i3inboundflow.py --resolve
  python scripts/genesys/build_i3inboundflow.py --check   # structural self-test only

Outputs
  architect/Branch-Inbound-Main.i3InboundFlow   ← import this
  architect/Branch-Inbound-Main.flow.json       ← decoded, for review / diff
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_I3 = REPO_ROOT / "architect" / "Branch-Inbound-Main.i3InboundFlow"
OUT_JSON = REPO_ROOT / "architect" / "Branch-Inbound-Main.flow.json"

FLOW_NAME = "Branch Inbound Main"
FLOW_DESC = ("Dynamic branch IVR: data table lookup on the dialed DID, common 3-option menu, "
             "option 3 transfers to the branch's Teams Auto Attendant number.")
LANG = "en-AU"
QUEUE_ROS = "ROS"
QUEUE_CB = "CB"
DATATABLE = "BranchDirectory"

NS = uuid.UUID("6f1d2c4e-5b7a-4c3d-9e8f-0a1b2c3d4e5f")  # stable ids → stable diffs


def uid(key: str) -> str:
    return str(uuid.uuid5(NS, key))


# ----------------------------------------------------------------------------
# Expression AST → Architect config (with 1-based character positions)
# ----------------------------------------------------------------------------
class Node:
    type: str

    def render(self, pos: int, refs: list) -> tuple[str, dict]:
        raise NotImplementedError


class Lit(Node):
    def __init__(self, value, type_):
        self.value, self.type = value, type_

    def render(self, pos, refs):
        if self.type == "str":
            text = '"' + self.value.replace("\\", "\\\\").replace('"', '\\"') + '"'
            return text, {"lit": {"pos": pos, "text": self.value, "type": "str"}}
        if self.type == "bln":
            text = "true" if self.value else "false"
        else:
            text = str(self.value)
        return text, {"lit": {"pos": pos, "text": text, "type": self.type}}


class Ref(Node):
    def __init__(self, var: dict):
        self.var, self.type = var, var["type"]

    def render(self, pos, refs):
        v = self.var
        key = "sysref" if v.get("sys") else "ref"
        if all(r["id"] != v["id"] for r in refs):
            refs.append({"id": v["id"], "type": v["type"], "isSysRef": bool(v.get("sys")), "name": v["name"]})
        return v["name"], {key: {"pos": pos, "text": v["name"], "type": v["type"], "val": v["id"]}}


class Fn(Node):
    def __init__(self, name, type_, *args):
        self.name, self.type, self.args = name, type_, list(args)

    def render(self, pos, refs):
        text = self.name + "("
        cur = pos + len(text)
        operands = []
        for i, a in enumerate(self.args):
            t, c = a.render(cur, refs)
            operands.append(c)
            text += t
            cur += len(t)
            if i < len(self.args) - 1:
                text += ", "
                cur += 2
        text += ")"
        return text, {self.name: {"pos": pos, "operands": operands, "type": self.type}}


class Op(Node):
    def __init__(self, sym, type_, left, right):
        self.sym, self.type, self.left, self.right = sym, type_, left, right

    def render(self, pos, refs):
        lt, lc = self.left.render(pos, refs)
        op_pos = pos + len(lt) + 1
        rt, rc = self.right.render(op_pos + len(self.sym) + 1, refs)
        return f"{lt} {self.sym} {rt}", {self.sym: {"pos": op_pos, "operands": [lc, rc], "type": self.type}}


def S(s): return Lit(s, "str")
def I(n): return Lit(n, "int")
def B(b): return Lit(b, "bln")
def And(a, b): return Op("and", "bln", a, b)
def Eq(a, b): return Op("==", "bln", a, b)
def Ne(a, b): return Op("!=", "bln", a, b)
def Cat(a, b): return Op("+", "str", a, b)


def expr_value(node: Node, name: str | None = None) -> dict:
    refs: list = []
    text, config = node.render(1, refs)
    if isinstance(node, (Fn, Op)):
        next(iter(config.values()))["text"] = text
    v = {"config": config, "text": text, "type": node.type, "uiMetaData": {"mode": 0},
         "metaData": {"references": refs} if refs else {}, "version": 2}
    if name:
        v["name"] = name
    return v


def lit_value(type_, text) -> dict:
    return {"config": {"lit": {"pos": 1, "text": text, "type": type_}}, "text": text, "type": type_,
            "uiMetaData": {"mode": 1}, "metaData": {}, "version": 2}


def emp_value(type_) -> dict:
    return {"config": {"emp": {"pos": 1, "text": "", "type": type_}}, "text": "", "type": type_,
            "uiMetaData": {"mode": 3}, "metaData": {}, "version": 2}


def var_value(var: dict) -> dict:
    """A variable selector (Update Data target / lookup output)."""
    return {"config": {"ref": {"pos": 1, "text": var["name"], "type": var["type"], "val": var["id"]}},
            "text": var["name"], "type": var["type"], "uiMetaData": {"mode": 2},
            "metaData": {"references": [{"id": var["id"], "type": var["type"], "isSysRef": False, "name": var["name"]}]},
            "version": 2}


def queue_value(name: str, qid: str) -> dict:
    return {"config": {"lit": {"pos": 1, "text": name, "type": "que", "val": qid}}, "text": name, "type": "que",
            "uiMetaData": {"mode": 1}, "metaData": {}, "version": 2}


def dur(seconds: int) -> str:
    return f"P0D0H0M{seconds}S0F"


# ----------------------------------------------------------------------------
# Audio blocks
# ----------------------------------------------------------------------------
def audio_tts(text: str, optional=False) -> dict:
    node = Fn("AudioPlaybackOptions", "aud", Fn("ToAudioTTS", "aud", S(text)), B(True))
    refs: list = []
    t, c = node.render(1, refs)
    c["AudioPlaybackOptions"]["text"] = t
    ui = {"sequenceItems": [{"type": 0, "parameter": text, "error": ""}], "customExpressionMode": False}
    if optional:
        ui["optional"] = True
    return _audio_wrap({"config": c, "text": t, "type": "aud", "uiMetaData": ui, "metaData": {}})


def audio_expr(node: Node) -> dict:
    refs: list = []
    t, c = node.render(1, refs)
    next(iter(c.values()))["text"] = t
    return _audio_wrap({"config": c, "text": t, "type": "aud",
                        "uiMetaData": {"sequenceItems": [], "customExpressionMode": True},
                        "metaData": {"references": refs} if refs else {}})


def audio_empty(optional=False) -> dict:
    ui = {"sequenceItems": [{"type": 0, "parameter": ""}], "customExpressionMode": False}
    if optional:
        ui["optional"] = True
    return _audio_wrap({"config": {"emp": {"pos": 1, "text": "", "type": "aud"}}, "text": "", "type": "aud",
                        "uiMetaData": ui, "metaData": {}})


def _audio_wrap(default_audio: dict) -> dict:
    return {"bargeInExpression": lit_value("bln", "true"), "flushExpression": lit_value("bln", "false"),
            "defaultAudio": default_audio, "cases": []}


# ----------------------------------------------------------------------------
# Flow builder
# ----------------------------------------------------------------------------
class Flow:
    def __init__(self, ids: dict):
        self.ids = ids
        self._tracking = 10
        self.variables: list = []
        self.vars: dict = {}
        self.manifest_ctx: dict = {"queue": {}, "dataTable": {}}

    def tid(self) -> int:
        self._tracking += 1
        return self._tracking - 1

    # variables -------------------------------------------------------------
    def var(self, name, type_, initial=None):
        v = {"name": name, "type": type_, "id": uid("var:" + name)}
        init = emp_value(type_) if initial is None else lit_value(type_, initial)
        self.variables.append({
            "id": v["id"], "isCollection": False, "isInternal": False, "isOutput": False, "isInput": False,
            "isSecure": False, "version": "1.0", "__type": {"str": "StringVariable", "bln": "BoolVariable"}[type_],
            "name": name, "initialValue": init})
        self.vars[name] = v
        return v

    @staticmethod
    def sysvar(name, sid, type_):
        return {"name": name, "id": sid, "type": type_, "sys": True}

    # actions ---------------------------------------------------------------
    def action(self, type_, name, key, **extra):
        a = {"trackingId": self.tid(), "id": uid("action:" + key), "name": name,
             "uiMetaData": {"collapsed": False}, "__type": type_}
        a.update(extra)
        return a

    @staticmethod
    def chain(actions: list) -> list:
        for a, b in zip(actions, actions[1:]):
            a["nextAction"] = b["id"]
        return actions

    @staticmethod
    def error_bindings():
        return [{"name": "errorType", "value": emp_value("str")}, {"name": "errorMessage", "value": emp_value("str")}]

    def update_data(self, name, key, assignments):
        return self.action("UpdateVariableAction", name, key, variables=[
            {"expression": expr, "variable": var_value(var)} for var, expr in assignments])

    def set_participant_data(self, name, key, attrs):
        return self.action("SetAttributesAction", name, key, variables=[
            {"expression": expr, "variable": lit_value("str", attr)} for attr, expr in attrs])

    def play_audio(self, name, key, prompts):
        return self.action("PlayAudioAction", name, key, prompts=prompts)

    def disconnect(self, name, key):
        return self.action("DisconnectAction", name, key)

    def jump_to_menu(self, name, key, menu_id):
        return self.action("TransferMenuAction", name, key, menuReference=menu_id)

    def decision(self, name, key, cond: Node, yes: list, no: list):
        yes, no = self.chain(yes), self.chain(no)
        paths = [{"label": "Yes", "outputId": "__YES__"}, {"label": "No", "outputId": "__NO__"}]
        if yes:
            paths[0]["nextActionId"] = yes[0]["id"]
        if no:
            paths[1]["nextActionId"] = no[0]["id"]
        a = self.action("DecisionAction", name, key, expression=expr_value(cond), paths=paths)
        a["_children"] = yes + no
        return a

    def transfer_to_acd(self, name, key, queue_name, pre_tts, fail_tts, failure: list):
        failure = self.chain(failure)
        qid = self.ids["queue"][queue_name]
        self.manifest_ctx["queue"].setdefault((queue_name, qid), []).append(
            {"actionName": "Transfer to ACD", "id": uid("action:" + key), "name": name})
        path = {"label": "Failure", "outputId": "__FAILURE__"}
        if failure:
            path["nextActionId"] = failure[0]["id"]
        a = self.action(
            "TransferPureMatchAction", name, key, useDefaultHandling=not failure,
            appendSkills=lit_value("bln", "false"), languageSkill=emp_value("lac"),
            preferredAgents=emp_value("cpx_agent_score_pair_coll"), priority=lit_value("int", "0"),
            queues=[queue_value(queue_name, qid)], skills=[], errorBindings=self.error_bindings(),
            preTransferAudio=audio_tts(pre_tts) if pre_tts else audio_empty(),
            failureTransferAudio=audio_tts(fail_tts), paths=[path])
        a["_children"] = failure
        return a

    def transfer_to_number(self, name, key, number: Node, pre_audio: dict, fail_tts, failure: list):
        failure = self.chain(failure)
        path = {"label": "Failure", "outputId": "__FAILURE__"}
        if failure:
            path["nextActionId"] = failure[0]["id"]
        a = self.action(
            "TransferExternalAction", name, key, externalNumber=expr_value(number),
            releaseLinkTransfer=lit_value("bln", "false"), connTimeout=emp_value("dur"),
            errorBindings=self.error_bindings(), preTransferAudio=pre_audio,
            failureTransferAudio=audio_tts(fail_tts), paths=[path])
        a["_children"] = failure
        return a

    def data_table_lookup(self, name, key, lookup: Node, outputs: list, found: list, not_found: list, failure: list):
        found, not_found, failure = self.chain(found), self.chain(not_found), self.chain(failure)
        dt_id = self.ids["dataTable"][DATATABLE]
        self.manifest_ctx["dataTable"].setdefault((DATATABLE, dt_id), []).append(
            {"actionName": "Data Table Lookup", "id": uid("action:" + key), "name": name})
        paths = [{"label": "Found", "outputId": "__FOUND__"}, {"label": "Not Found", "outputId": "__NOT_FOUND__"},
                 {"label": "Failure", "outputId": "__FAILURE__"}]
        for p, acts in zip(paths, (found, not_found, failure)):
            if acts:
                p["nextActionId"] = acts[0]["id"]
        a = self.action(
            "DataTableLookupAction", name, key, datatableId=dt_id, datatableName=DATATABLE,
            lookupKeyValue=expr_value(lookup, name="lookupKeyValue"),
            outputs=[{"name": col, "value": var_value(var)} for col, var in outputs],
            errorBindings=self.error_bindings(), paths=paths)
        a["_children"] = found + not_found + failure
        return a

    # containers ------------------------------------------------------------
    @staticmethod
    def _flatten(actions: list) -> list:
        out = []
        for a in actions:
            kids = a.pop("_children", [])
            out.append(a)
            out.extend(Flow._flatten(kids))
        return out

    def task(self, name, key, actions: list, variables=None):
        actions = self.chain(actions)
        return {"startAction": actions[0]["id"], "trackingId": self.tid(), "id": uid("task:" + key), "name": name,
                "uiMetaData": {"collapsed": False}, "__type": "Task",
                "paths": [{"label": "Default", "outputId": "__DEFAULT__"}],
                "actionList": self._flatten(actions), "variables": variables or []}

    def menu(self, name, key, prompts, choices: list, default_choice_key: str, repeat=3, timeout_s=6):
        choice_list = []
        for digit, cname, ckey, action in choices:
            action.pop("_children", None)
            choice_list.append({"globalSpeech": False, "speechRecTerms": {LANG: []}, "id": uid("choice:" + ckey),
                                "name": cname, "action": action, "digit": digit})
        return {"trackingId": self.tid(), "id": uid("menu:" + key), "name": name, "uiMetaData": {"collapsed": False},
                "__type": "Menu", "extensionDialingDelay": emp_value("dur"), "invalidSpeechRecPromptsList": [],
                "menuChoiceList": choice_list, "defaultMenuChoice": uid("choice:" + default_choice_key),
                "promptRepeatCount": lit_value("int", str(repeat)),
                "promptRepeatDelay": lit_value("dur", dur(timeout_s)), "prompts": prompts,
                "speechRecMinConfidence": emp_value("int"), "speechRecTimeoutComplete": emp_value("dur"),
                "speechRecTimeoutIncomplete": emp_value("dur"), "speechRecTimeoutTotal": emp_value("dur"),
                "useExtensionDialing": lit_value("bln", "false")}

    def task_choice(self, name, key, task_id):
        return self.action("TaskAction", name, key, taskReference=task_id)


def default_settings() -> dict:
    return {
        "PlayAudioOnSilenceAction": {"allowedRepeatCount": lit_value("int", "5"),
                                     "silenceDuration": lit_value("dur", dur(1)), "timeout": lit_value("dur", dur(40))},
        "DetectSilenceAction": {"silenceDuration": lit_value("dur", dur(1)), "timeout": lit_value("dur", dur(40))},
        "DataAction": {"processingPrompt": emp_value("pmt")},
        "CollectInputAction": {"interDigitTimeout": lit_value("dur", dur(3)), "noEntryTimeout": lit_value("dur", dur(5))},
        "DialExtensionAction": {"digitTimeout": lit_value("dur", dur(6))},
        "TransferUserAction": {"connTimeout": emp_value("dur")},
        "TransferExternalAction": {"connTimeout": emp_value("dur")},
        "TransferGroupAction": {"connTimeout": emp_value("dur")},
        "TransferFlowSecureAction": {"connTimeout": lit_value("dur", dur(15))},
        "Menu": {"promptRepeatCount": lit_value("int", "3"), "promptRepeatDelay": lit_value("dur", dur(10)),
                 "extensionDialingDelay": lit_value("dur", dur(1)), "useExtensionDialing": lit_value("bln", "false")},
        "SpeechRec": {"speechRecMinConfidence": lit_value("int", "50"),
                      "speechRecTimeoutComplete": lit_value("dur", "P0D0H0M0S100F"),
                      "speechRecTimeoutIncomplete": lit_value("dur", "P0D0H0M0S1500F"),
                      "speechRecTimeoutTotal": lit_value("dur", dur(20))},
    }


# ----------------------------------------------------------------------------
def build(ids: dict) -> dict:
    f = Flow(ids)

    # ---- variables (not-found keeps these defaults: generic greeting, no Teams number) ----
    code = f.var("Flow.BranchCode", "str")
    bname = f.var("Flow.BranchName", "str", "our bank")
    teams = f.var("Flow.TeamsDID", "str")
    called_orig = Flow.sysvar("Call.CalledAddressOriginal", "__CALL_DNIS_ORIGINAL__", "str")

    # ---- Task: Initialise  (lookup -> participant data -> menu) ----------
    lookup = f.data_table_lookup(
        "Lookup BranchDirectory", "lookup",
        Fn("Replace", "str", Ref(called_orig), S("tel:"), S("")),
        outputs=[("BranchCode", code), ("BranchName", bname), ("TeamsDID", teams)],
        found=[], not_found=[], failure=[])
    attach = f.set_participant_data("Set branch participant data", "setpd", [
        ("BranchName", expr_value(Ref(bname))), ("BranchCode", expr_value(Ref(code)))])
    goto_menu = f.jump_to_menu("Go to Main Menu", "gotomenu", uid("menu:main"))
    init_task = f.task("Initialise", "init", [lookup, attach, goto_menu])

    # ---- Task: Transfer to Branch  (decision -> Teams, else ROS) ----------
    fail_tts = "We are sorry, we are unable to connect you right now. Please try again later."
    has_teams = Ne(Fn("If", "str", Fn("IsSet", "bln", Ref(teams)), Ref(teams), S("")), S(""))
    yes_branch = [f.transfer_to_number(
        "Transfer to branch Teams DID", "teams", Ref(teams),
        pre_audio=audio_expr(Fn("ToAudioTTS", "aud", Cat(Cat(S("Transferring you to the "), Ref(bname)), S(" branch now.")))),
        fail_tts="We are sorry, we could not connect you to the branch.",
        failure=[f.transfer_to_acd("Fallback to " + QUEUE_ROS, "teamsfail.ros", QUEUE_ROS,
                                   "Please hold while we connect you to our team.", fail_tts, failure=[])])]
    no_branch = [f.transfer_to_acd(
        "Branch unavailable - route to " + QUEUE_ROS, "unavail.ros", QUEUE_ROS,
        "The branch is not available right now. Please hold while we connect you to our team.", fail_tts, failure=[])]
    branch_task = f.task("Transfer to Branch", "branch",
                         [f.decision("Branch has a Teams number?", "decide", has_teams, yes_branch, no_branch)])

    # ---- Menu --------------------------------------------------------------
    greeting = audio_expr(Fn("ToAudioTTS", "aud", Cat(Cat(S("Thank you for calling "), Ref(bname)), S(
        ". For R O S, press 1. For Country Banking, press 2. To speak to the branch, press 3."))))
    main_menu = f.menu("Main Menu", "main", greeting, [
        (1, QUEUE_ROS, "ros", f.transfer_to_acd("Transfer to " + QUEUE_ROS, "menu.ros", QUEUE_ROS,
                                                 "Please hold while we connect you.", fail_tts, failure=[])),
        (2, "Country Banking", "cb", f.transfer_to_acd("Transfer to " + QUEUE_CB, "menu.cb", QUEUE_CB,
                                                        "Please hold while we connect you to Country Banking.", fail_tts, failure=[])),
        (3, "Speak to the Branch", "branch", f.task_choice("Speak to the Branch", "menu.branch", branch_task["id"])),
    ], default_choice_key="ros")

    # ---- flow --------------------------------------------------------------
    manifest = {
        "queue": [{"name": n, "id": i, "context": ctx} for (n, i), ctx in f.manifest_ctx["queue"].items()],
        "dataTable": [{"name": n, "id": i, "context": ctx} for (n, i), ctx in f.manifest_ctx["dataTable"].items()],
        "language": [{"id": LANG}], "userPrompt": [], "systemPrompt": [],
    }
    return {
        "defaultLanguage": LANG, "description": FLOW_DESC, "initialSequence": init_task["id"], "name": FLOW_NAME,
        "nextTrackingNumber": f._tracking, "uiMetaData": {"bridgeServerActions": [], "screenPops": []},
        "supportedLanguages": [LANG], "manifest": manifest, "type": "inboundcall", "isSecure": False,
        "debugSettings": {},
        "disableAsr": lit_value("bln", "false"), "disableAsrCompanyDirectory": lit_value("bln", "true"),
        "errorHandling": {
            "audio": audio_tts("We are sorry, we are experiencing technical difficulties. Please call back later.", optional=True),
            "queue": {**emp_value("que"), "uiMetaData": {"mode": 1}}, "handlingType": "Disconnect"},
        "initialPrompts": audio_empty(optional=True),
        "speechRecCompanyDirectory": lit_value("bln", "false"),
        "speechRecEngine": {"config": {"lit": {"pos": 1, "text": "", "type": "str"}}, "text": "", "type": "str",
                            "uiMetaData": {"mode": 1}, "metaData": {}},
        "suppressRecording": lit_value("bln", "false"),
        "customDefinitions": {"dataTypeDefinitions": []},
        "defaultSettings": default_settings(),
        "flowMetaData": {"flowDocumentVersion": "1.0", "minimumServerVersion": "1.0", "ttsDataVersion": "1.0"},
        "flowSequenceItemList": [init_task, main_menu, branch_task],
        "supportedLanguageOptions": [{"language": LANG, "languageSkill": emp_value("lac")}],
        "variables": f.variables,
    }


# ----------------------------------------------------------------------------
def encode(obj: dict) -> str:
    text = json.dumps(obj, separators=(",", ":"))
    return base64.b64encode(urllib.parse.quote(text, safe="!~*'()").encode("utf-8")).decode("ascii")


def check(flow: dict) -> None:
    """Structural self-test: every reference resolves, tracking ids are unique."""
    ids, tracking, refs = set(), [], []

    def walk(o):
        if isinstance(o, dict):
            if "__type" in o and "id" in o:
                ids.add(o["id"])
            if "trackingId" in o:
                tracking.append(o["trackingId"])
            for k in ("nextAction", "nextActionId", "taskReference", "menuReference", "startAction", "defaultMenuChoice"):
                if k in o:
                    refs.append((k, o[k]))
            if "config" in o and isinstance(o["config"], dict):
                for r in o.get("metaData", {}).get("references", []):
                    if not r["isSysRef"]:
                        refs.append(("var", r["id"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(flow)
    ids |= {v["id"] for v in flow["variables"]}
    for item in flow["flowSequenceItemList"]:
        if item["__type"] == "Menu":
            ids |= {c["id"] for c in item["menuChoiceList"]}
    missing = [(k, v) for k, v in refs if v not in ids]
    assert not missing, f"dangling references: {missing}"
    assert len(tracking) == len(set(tracking)), "duplicate trackingId"
    assert max(tracking) < flow["nextTrackingNumber"], "nextTrackingNumber too small"
    assert flow["initialSequence"] in ids
    print(f"check ok: {len(ids)} objects, {len(refs)} references, {len(tracking)} tracking ids")


def resolve_ids() -> dict:
    import requests  # only needed for --resolve

    region = os.environ.get("GENESYS_REGION", "mypurecloud.com.au")
    cid, secret = os.environ.get("GENESYS_CLIENT_ID"), os.environ.get("GENESYS_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("--resolve needs GENESYS_CLIENT_ID and GENESYS_CLIENT_SECRET")
    tok = requests.post(f"https://login.{region}/oauth/token", data={"grant_type": "client_credentials"},
                        auth=(cid, secret), timeout=30)
    tok.raise_for_status()
    h = {"Authorization": "Bearer " + tok.json()["access_token"]}
    base = f"https://api.{region}"

    def one(path, name, params):
        r = requests.get(base + path, headers=h, params=params, timeout=30)
        r.raise_for_status()
        hits = [e for e in r.json().get("entities", []) if e["name"] == name]
        if not hits:
            sys.exit(f"{name} not found in org via {path}")
        return hits[0]["id"]

    return {"queue": {q: one("/api/v2/routing/queues", q, {"name": q, "pageSize": 100}) for q in (QUEUE_ROS, QUEUE_CB)},
            "dataTable": {DATATABLE: one("/api/v2/flows/datatables", DATATABLE, {"name": DATATABLE, "pageSize": 100})}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolve", action="store_true", help="look up real queue / data table IDs in the org")
    ap.add_argument("--check", action="store_true", help="build and self-test only, write nothing")
    args = ap.parse_args()

    ids = resolve_ids() if args.resolve else {
        "queue": {QUEUE_ROS: uid("queue:ROS"), QUEUE_CB: uid("queue:CB")},
        "dataTable": {DATATABLE: uid("datatable:BranchDirectory")}}
    flow = build(ids)
    check(flow)
    if args.check:
        return
    OUT_JSON.write_text(json.dumps(flow, indent=2) + "\n", encoding="utf-8")
    OUT_I3.write_text(encode(flow), encoding="ascii")
    print(f"wrote {OUT_I3.relative_to(REPO_ROOT)} ({OUT_I3.stat().st_size} bytes)")
    print(f"wrote {OUT_JSON.relative_to(REPO_ROOT)}")
    if not args.resolve:
        print("note: placeholder queue / data table IDs - re-select ROS, CB and BranchDirectory in Architect after import,"
              " or re-run with --resolve.")


if __name__ == "__main__":
    main()
