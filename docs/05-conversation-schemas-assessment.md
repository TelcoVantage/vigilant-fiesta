# 05 – Should the flow use Conversation Schemas?

**Short answer: not for go-live. Keep participant data. Revisit when Genesys
ships native Scripter and Analytics support for conversation attributes.**

## What conversation schemas are

Genesys Cloud *Custom Data Management → Conversation Schemas* lets an admin
define a typed, JSON-based blueprint of business fields (e.g. `branchCode`,
`branchName`, `teamsTransferAttempted`) and attach records built from that
schema to a conversation. Key properties, from the Genesys documentation:

| Property | Conversation attributes (schemas) | Participant data (what the flow uses today) |
|----------|-----------------------------------|---------------------------------------------|
| Typing | Schema-governed, nine data types, nested objects allowed | Strings only, free-form names |
| Scope | Conversation level | Per participant, travels with the participant through transfers |
| Set from Architect | Native *Create / Update / Get Conversation Attributes* actions | *Set / Get Participant Data* actions |
| Visible in agent script | **Not yet** – "in a future release, agent scripts will include native actions" | Yes, bind a script field to the attribute today |
| Filterable in Performance → Interactions | **Not yet** – query via the Conversation Attribute Search API; native analytics support is "a future release" | Yes, today (attribute columns / filters) |
| Retention | Rolling 365 days | Lifetime of the conversation record |
| Licensing / permissions | CX 2, CX 2 Digital or CX 3 Digital **plus** Conversation > Custom Attributes > View/Add/Edit | None beyond Architect |
| Import of this flow | Adds an action type with org-specific schema IDs to re-select after import | Nothing extra |

Genesys's own positioning: participant data is "ideal for cross-flow logic and
runtime decisions rather than analytics or reporting"; conversation / customer
profile schemas are for "better search, filtering, and analysis".

## What this flow actually needs the data for

| Need | Today's solution | Does a schema help *now*? |
|------|------------------|---------------------------|
| Agent in ROS/CB sees which branch the customer dialed (screen pop) | Participant data `BranchName` / `BranchCode` bound in the script | **No** – scripts cannot read conversation attributes yet |
| Supervisors filter interactions by branch | Participant data attribute columns in Performance → Interactions | **No** – not queryable in analytics views yet; API only |
| Spot DIDs missing from the data table | `BranchFound=false` attribute | **No** – same as above |
| Routing decisions inside the flow | Flow variables | **No** – flow variables are the right tool either way |
| Long-term, typed, cross-channel branch reporting once native analytics lands | – | **Yes, later** |

So for the two things the data is used for on day one – the agent's screen and
the supervisor's interaction view – conversation attributes are currently
invisible. Adopting them now would mean writing the same five fields twice
(participant data for today, schema record for the future) for no visible
benefit, plus a licence/permission dependency and one more org-specific
reference to fix up on every import.

## Recommendation

1. **Go live on participant data** as built. It is the only option that works
   end to end today (flow → script → analytics).
2. **Define the schema now but don't wire it in.** A draft is in
   `data-tables/branch-context.conversation-schema.json` so the field names and
   types are agreed early and match the participant data keys one-for-one.
   Create it under Admin → Custom Data Management → Conversation Schemas when
   you want to start capturing records.
3. **Trigger to revisit:** when the Genesys release notes announce native
   conversation-attribute support in *Scripter* and in *Performance →
   Interactions* filters. At that point add one *Create Conversation
   Attributes* action immediately after *Set Participant Data* in the
   `Initialise` task (dual-write for a release cycle), move the script binding
   and reports across, then drop the participant data action. It is a one-task
   change to one flow; nothing per branch.

## If you want to adopt it anyway (e.g. an org-wide custom-data standard)

* Licence: confirm the agents' licence tier qualifies (CX 2 / CX 2 Digital /
  CX 3 Digital).
* Permissions on the Architect publisher's role: Conversation > Custom
  Attributes > View, Add, Edit.
* Create the schema from the draft JSON, then in Architect add **Create
  Conversation Attributes** after *Set Participant Data* in `Initialise`, map
  `branchCode`, `branchName`, `branchState`, `dialedDid`, `branchFound` from
  the same `Flow.*` variables. Keep *Set Participant Data* until the script
  and reports no longer need it.
* The `.i3InboundFlow` generator in this repo does not emit that action (no
  reference export of it was available to model the JSON on), so add it in
  the Architect UI after import and re-export from your org if you want it in
  version control.

Sources: Genesys Cloud Resource Center – *Conversation schemas overview*,
*About custom data management*, *Participant data overview*, *Attach custom
conversation attributes to participant data in Architect*.
