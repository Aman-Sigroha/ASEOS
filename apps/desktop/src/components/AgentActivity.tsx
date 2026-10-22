import React, { useEffect, useRef } from "react";
import {
  Bot,
  User,
  Sparkles,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Clock,
  FileCode,
  FileCheck2,
  ArrowRight,
  RotateCcw,
  Search,
  ListOrdered,
  FlaskConical,
  Check,
  ChevronRight,
  ExternalLink,
  PanelRightClose,
} from "lucide-react";
import type { AgentEvent, EventType } from "../types/event";
import type { TaskResult } from "../types/execution";

export type AgentActivityProps = {
  events: AgentEvent[];
  isRunning: boolean;
  taskPrompt?: string;
  result?: TaskResult | null;
  onInspectChanges?: () => void;
  onInspectTests?: () => void;
  onReset?: () => void;
  onSelectPrompt?: (prompt: string) => void;
  onOpenFile?: (file: string) => void;
  onClosePanel?: () => void;
};

// Plan step definitions
const PLAN_STEPS = [
  { id: "understand", label: "Understand task & repository context" },
  { id: "search", label: "Scan workspace & search symbols" },
  { id: "plan", label: "Synthesize engineering plan" },
  { id: "execute", label: "Execute modifications in codebase" },
  { id: "verify", label: "Run test suite & verify regressions" },
];

function getEventIcon(eventType: EventType) {
  switch (eventType) {
    case "TASK_STARTED":
      return <Sparkles size={14} className="event-icon start" />;
    case "TASK_UNDERSTANDING_COMPLETED":
      return <Bot size={14} className="event-icon understanding" />;
    case "PLAN_CREATED":
      return <ListOrdered size={14} className="event-icon plan" />;
    case "ACTION_STARTED":
      return <Search size={14} className="event-icon action-start" />;
    case "ACTION_COMPLETED":
      return <CheckCircle2 size={14} className="event-icon action-done" />;
    case "ACTION_FAILED":
      return <AlertCircle size={14} className="event-icon action-failed" />;
    case "VERIFICATION_STARTED":
      return <FlaskConical size={14} className="event-icon verify" />;
    case "VERIFICATION_COMPLETED":
      return <CheckCircle2 size={14} className="event-icon verify-done" />;
    case "VERIFICATION_FAILED":
      return <AlertCircle size={14} className="event-icon verify-failed" />;
    case "TASK_COMPLETED":
      return <Check size={14} className="event-icon task-completed" />;
    case "TASK_FAILED":
      return <XCircle size={14} className="event-icon task-failed" />;
    default:
      return <Bot size={14} className="event-icon default" />;
  }
}

// Compute active step index based on current events
function getActiveStepIndex(events: AgentEvent[], isRunning: boolean, result: TaskResult | null) {
  if (result) return 5; // all done
  if (events.length === 0) return -1;
  const lastType = events[events.length - 1]?.event_type;

  if (lastType === "TASK_STARTED") return 0;
  if (lastType === "TASK_UNDERSTANDING_COMPLETED") return 1;
  if (lastType === "ACTION_STARTED" || lastType === "ACTION_COMPLETED") {
    // If we've passed plan created, we're in execute
    const hasPlan = events.some((e) => e.event_type === "PLAN_CREATED");
    return hasPlan ? 3 : 1;
  }
  if (lastType === "PLAN_CREATED") return 2;
  if (lastType === "VERIFICATION_STARTED") return 4;
  return isRunning ? 3 : 0;
}

export const AgentActivity: React.FC<AgentActivityProps> = ({
  events,
  isRunning,
  taskPrompt,
  result,
  onInspectChanges,
  onInspectTests,
  onReset,
  onSelectPrompt,
  onOpenFile,
  onClosePanel,
}) => {
  const scrollEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new events or state changes
  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length, isRunning, result]);

  const activeStep = getActiveStepIndex(events, isRunning, result ?? null);

  const formatTimestamp = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return iso;
    }
  };

  return (
    <aside className="agent-panel" aria-label="ASEOS Agent Activity">
      {/* Panel Top Header */}
      <div className="agent-header">
        <div className="agent-header-info">
          <div className="agent-avatar-badge">
            <Bot size={15} />
          </div>
          <div className="agent-title-group">
            <span className="agent-title">ASEOS Agent</span>
            <span className="agent-model-chip">Claude 3.7 Sonnet (Mock)</span>
          </div>
        </div>

        <div className="agent-header-actions">
          {/* Status pill */}
          <div
            className={`agent-status-pill ${
              isRunning ? "running" : result?.status === "COMPLETED" ? "completed" : result?.status === "FAILED" ? "failed" : "idle"
            }`}
          >
            <span className="status-dot" />
            <span className="status-text">
              {isRunning
                ? "Executing..."
                : result?.status === "COMPLETED"
                ? "Completed"
                : result?.status === "FAILED"
                ? "Failed"
                : "Ready"}
            </span>
          </div>

          {(events.length > 0 || taskPrompt) && !isRunning && onReset && (
            <button
              className="agent-reset-btn"
              onClick={onReset}
              title="Reset conversation"
              aria-label="Reset conversation"
            >
              <RotateCcw size={13} />
            </button>
          )}

          {onClosePanel && (
            <button
              className="agent-collapse-btn"
              onClick={onClosePanel}
              title="Collapse Agent Panel (Ctrl+L)"
              aria-label="Collapse Agent Panel"
            >
              <PanelRightClose size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Main Conversation & Timeline Area */}
      <div className="agent-conversation-area">
        {/* Welcome Empty State */}
        {events.length === 0 && !isRunning && !taskPrompt && (
          <div className="agent-empty-welcome">
            <div className="welcome-icon-box">
              <Sparkles size={24} className="welcome-sparkle" />
            </div>
            <h3 className="welcome-heading">How can ASEOS help you today?</h3>
            <p className="welcome-desc">
              I can inspect your repository, construct multi-step engineering plans, apply code changes, and verify results.
            </p>

            <div className="welcome-suggestions">
              <div className="suggestions-meta">TRY AN EXAMPLE TASK</div>
              <button
                className="welcome-card-prompt"
                onClick={() =>
                  onSelectPrompt?.("Update main.py to print greeting from ASEOS")
                }
              >
                <div className="prompt-title">Update main.py greeting</div>
                <div className="prompt-subtitle">Print "Hello from ASEOS" and verify</div>
                <ArrowRight size={13} className="prompt-arrow" />
              </button>

              <button
                className="welcome-card-prompt"
                onClick={() =>
                  onSelectPrompt?.("Refactor utils.py to include type annotations")
                }
              >
                <div className="prompt-title">Add type annotations to utils.py</div>
                <div className="prompt-subtitle">Type hinting for add and subtract functions</div>
                <ArrowRight size={13} className="prompt-arrow" />
              </button>
            </div>
          </div>
        )}

        {/* User Prompt Message */}
        {taskPrompt && (
          <div className="user-message-card">
            <div className="message-header">
              <div className="avatar-user">
                <User size={13} />
              </div>
              <span className="sender-name">User</span>
              <span className="message-time">Just now</span>
            </div>
            <div className="message-body">{taskPrompt}</div>
          </div>
        )}

        {/* Multi-step Plan Progress */}
        {(events.length > 0 || isRunning) && (
          <div className="agent-plan-card">
            <div className="plan-header">
              <ListOrdered size={14} className="plan-icon" />
              <span className="plan-title">Execution Plan</span>
              <span className="plan-step-counter">
                {Math.min(activeStep + 1, PLAN_STEPS.length)} of {PLAN_STEPS.length}
              </span>
            </div>

            <div className="plan-steps-list">
              {PLAN_STEPS.map((step, idx) => {
                const isStepDone = activeStep > idx;
                const isStepActive = activeStep === idx && isRunning;
                const isStepPending = activeStep < idx;

                return (
                  <div
                    key={step.id}
                    className={`plan-step-row ${
                      isStepDone ? "done" : isStepActive ? "active" : isStepPending ? "pending" : ""
                    }`}
                  >
                    <div className="step-indicator">
                      {isStepDone ? (
                        <Check size={12} className="check-svg" />
                      ) : isStepActive ? (
                        <span className="step-pulse" />
                      ) : (
                        <span className="step-num">{idx + 1}</span>
                      )}
                    </div>
                    <span className="step-label">{step.label}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Live Event Timeline */}
        {events.length > 0 && (
          <div className="agent-timeline">
            <div className="timeline-title-row">
              <span className="timeline-heading">ACTIVITY LOG ({events.length})</span>
              <span className="timeline-tag">Live Stream</span>
            </div>

            <div className="timeline-events-list">
              {events.map((event, index) => (
                <div key={event.event_id || index} className="timeline-event-item">
                  <div className="timeline-node">
                    {getEventIcon(event.event_type)}
                    <span className="timeline-line" />
                  </div>

                  <div className="timeline-content">
                    <div className="event-meta-line">
                      <span className="event-type-badge">
                        {event.event_type.replace(/_/g, " ")}
                      </span>
                      {event.action_id && (
                        <span className="action-id-tag">{event.action_id}</span>
                      )}
                      <span className="event-time">
                        <Clock size={10} />
                        {formatTimestamp(event.timestamp)}
                      </span>
                    </div>

                    <div className="event-message-text">{event.message}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Task Completion Card */}
        {result && (
          <div className={`result-summary-card ${result.status.toLowerCase()}`}>
            <div className="summary-card-header">
              {result.status === "COMPLETED" ? (
                <>
                  <CheckCircle2 size={16} className="status-icon success" />
                  <span className="summary-title">Task Finished Successfully</span>
                </>
              ) : (
                <>
                  <XCircle size={16} className="status-icon error" />
                  <span className="summary-title">Task Failed / Aborted</span>
                </>
              )}
              <span className="mock-badge" title="Mock Execution Environment">
                MOCK RESULT
              </span>
            </div>

            <p className="summary-text">{result.summary}</p>

            {/* Changed Files */}
            {result.changed_files.length > 0 && (
              <div className="summary-section">
                <div className="summary-section-label">
                  <FileCode size={12} />
                  <span>Changed Files ({result.changed_files.length}):</span>
                </div>
                <div className="changed-files-chips">
                  {result.changed_files.map((file) => (
                    <button
                      key={file}
                      className="changed-file-chip"
                      onClick={() => onOpenFile?.(file)}
                      title="Open file in editor"
                    >
                      <span>{file}</span>
                      <ExternalLink size={10} />
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Test Results */}
            {result.test_result && (
              <div className="summary-section">
                <div className="summary-section-label">
                  <FileCheck2 size={12} />
                  <span>Verification:</span>
                </div>
                <div className="test-results-row">
                  <span
                    className={`test-status-pill ${result.test_result.status.toLowerCase()}`}
                  >
                    {result.test_result.status}
                  </span>
                  <span className="test-counts">
                    {result.test_result.passed} passed, {result.test_result.failed} failed
                  </span>
                </div>
              </div>
            )}

            {/* Inspect buttons */}
            <div className="summary-actions">
              {onInspectChanges && (
                <button
                  className="inspect-btn primary"
                  onClick={onInspectChanges}
                  title="View side-by-side or unified diff"
                >
                  <FileCode size={13} />
                  <span>Inspect Changes</span>
                  <ChevronRight size={13} />
                </button>
              )}

              {onInspectTests && (
                <button
                  className="inspect-btn secondary"
                  onClick={onInspectTests}
                  title="Inspect test results and output"
                >
                  <FlaskConical size={13} />
                  <span>View Tests</span>
                </button>
              )}
            </div>
          </div>
        )}

        {/* Scroll anchor */}
        <div ref={scrollEndRef} style={{ height: 1 }} />
      </div>
    </aside>
  );
};

export default AgentActivity;