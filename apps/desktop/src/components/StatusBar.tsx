import React from "react";
import { GitBranch, CheckCircle2, AlertCircle, Bot, Bell } from "lucide-react";

export type StatusBarProps = {
  branch?: string;
  activeFile?: string;
  isRunning?: boolean;
  activeStep?: number;
  onToggleBottomPanel?: () => void;
  onToggleAgentPanel?: () => void;
};

export const StatusBar: React.FC<StatusBarProps> = ({
  branch = "feature/desktop-ui",
  activeFile = "src/main.py",
  isRunning = false,
  onToggleBottomPanel,
  onToggleAgentPanel,
}) => {
  const isPython = activeFile.endsWith(".py");
  const isMarkdown = activeFile.endsWith(".md");

  return (
    <footer className="ide-statusbar" role="contentinfo">
      {/* Left group */}
      <div className="statusbar-group left">
        <button
          className="statusbar-item-btn"
          title={`Git Branch: ${branch}`}
        >
          <GitBranch size={12} />
          <span>{branch}*</span>
        </button>

        <button
          className="statusbar-item-btn"
          onClick={onToggleBottomPanel}
          title="Toggle Bottom Panel (0 Errors, 0 Warnings)"
        >
          <CheckCircle2 size={12} className="status-icon-good" />
          <span>0</span>
          <AlertCircle size={12} className="status-icon-muted" />
          <span>0</span>
        </button>
      </div>

      {/* Center group */}
      <div className="statusbar-group center">
        <button
          className={`statusbar-agent-indicator ${isRunning ? "running" : ""}`}
          onClick={onToggleAgentPanel}
          title="Toggle ASEOS Agent Panel (Ctrl+L)"
        >
          <Bot size={12} />
          <span>{isRunning ? "ASEOS Agent: Processing task..." : "ASEOS Agent: Ready"}</span>
        </button>
      </div>

      {/* Right group */}
      <div className="statusbar-group right">
        <span className="statusbar-text">Ln 1, Col 1</span>
        <span className="statusbar-text">Spaces: 4</span>
        <span className="statusbar-text">UTF-8</span>
        <span className="statusbar-text highlight">
          {isPython ? "Python 3.11" : isMarkdown ? "Markdown" : "Plain Text"}
        </span>
        <button className="statusbar-item-btn icon-only" title="Notifications">
          <Bell size={12} />
        </button>
      </div>
    </footer>
  );
};

export default StatusBar;
