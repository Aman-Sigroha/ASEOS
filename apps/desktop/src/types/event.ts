export type EventType =
  | "TASK_STARTED"
  | "TASK_UNDERSTANDING_COMPLETED"
  | "PLAN_CREATED"
  | "ACTION_STARTED"
  | "ACTION_COMPLETED"
  | "ACTION_FAILED"
  | "VERIFICATION_STARTED"
  | "VERIFICATION_COMPLETED"
  | "VERIFICATION_FAILED"
  | "TASK_COMPLETED"
  | "TASK_FAILED";

export type AgentEvent = {
  event_id: string;
  task_id: string;
  event_type: EventType;
  timestamp: string;
  action_id: string | null;
  message: string;
  data: Record<string, unknown>;
};