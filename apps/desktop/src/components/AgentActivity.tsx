import type { AgentEvent } from "../types/event";

type AgentActivityProps = {
  events: AgentEvent[];
  isRunning: boolean;
};

function AgentActivity({
  events,
  isRunning,
}: AgentActivityProps) {
  return (
    <aside className="agent-panel">
      <div className="panel-title">
        AGENT ACTIVITY
      </div>

      {events.length === 0 ? (
        <div className="empty-events">
          No agent activity yet.
        </div>
      ) : (
        events.map((event, index) => (
          <div
            className="agent-event"
            key={event.event_id}
          >
            <span className="event-number">
              {index + 1}
            </span>

            <span>{event.message}</span>
          </div>
        ))
      )}

      <div className="agent-status">
        <span
          className={`status-dot ${
            isRunning ? "running" : ""
          }`}
        >
          ●
        </span>{" "}
        {isRunning
          ? "Agent running..."
          : "Agent ready"}
      </div>
    </aside>
  );
}

export default AgentActivity;