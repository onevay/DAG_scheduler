// Короткие имена для типов API; сами типы генерируются из schemas/openapi.json (`npm run gen:types`).
import type { components } from "./schema";

type S = components["schemas"];

export type Problem = S["ProblemIn"];
export type Task = S["TaskIn"];
export type Worker = S["WorkerIn"];
export type InputEvent = S["EventIn"];
export type Policy = S["PolicyIn"];
export type PolicyName = Policy["name"];

export type Result = S["ResultOut"];
export type Metrics = S["MetricsOut"];
export type Attempt = S["AttemptOut"];
export type TaskSummary = S["TaskOut"];
export type Diagnostic = S["DiagnosticOut"];
export type JournalRecord = S["EventsPage"]["events"][number];

export type Policies = S["PoliciesOut"];
export type Validation = S["ValidationOut"];
export type Scenario = S["ScenarioInfo"];
export type RunListItem = S["RunListItem"];
export type Run = S["RunOut"];
export type EventsPage = S["EventsPage"];
export type ExplainView = S["ExplainView"];
export type Intervention = S["WhatIfRequest"]["interventions"][number];
export type WhatIf = S["WhatIfOut"];
export type ErrorBody = S["ErrorOut"];
export type TaskStatus = S["TaskStatus"];
export type TaskKind = NonNullable<Task["kind"]>;
