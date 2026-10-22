import React from "react";
import { Files, Search, GitBranch, TerminalSquare, Bot, Settings } from "lucide-react";

export type SidebarTab = "explorer" | "search" | "git" | "terminal";

export type ActivityBarProps = {
  activeTab: SidebarTab;
  onSelectTab: (tab: SidebarTab) => void;
  onOpenSettings?: () => void;
  gitChangeCount?: number;
  agentPanelVisible?: boolean;
  onToggleAgentPanel?: () => void;
};

export const ActivityBar: React.FC<ActivityBarProps> = ({
  activeTab,
  onSelectTab,
  onOpenSettings,
  gitChangeCount = 1,
  agentPanelVisible = true,
  onToggleAgentPanel,
}) => {
  return (
    <aside className="activity-bar" aria-label="Activity Bar">
      <div className="activity-bar-group top">
        <button
          className={`activity-icon-btn ${activeTab === "explorer" ? "active" : ""}`}
          onClick={() => onSelectTab("explorer")}
          title="Explorer (Ctrl+Shift+E)"
          aria-label="Explorer"
        >
          <Files size={18} />
          {activeTab === "explorer" && <span className="active-indicator" />}
        </button>

        <button
          className={`activity-icon-btn ${activeTab === "search" ? "active" : ""}`}
          onClick={() => onSelectTab("search")}
          title="Search (Ctrl+Shift+F)"
          aria-label="Search"
        >
          <Search size={18} />
          {activeTab === "search" && <span className="active-indicator" />}
        </button>

        <button
          className={`activity-icon-btn ${activeTab === "git" ? "active" : ""}`}
          onClick={() => onSelectTab("git")}
          title="Source Control (Ctrl+Shift+G)"
          aria-label="Source Control"
        >
          <GitBranch size={18} />
          {gitChangeCount > 0 && (
            <span className="activity-badge" title={`${gitChangeCount} pending change`}>
              {gitChangeCount}
            </span>
          )}
          {activeTab === "git" && <span className="active-indicator" />}
        </button>

        <button
          className={`activity-icon-btn ${activeTab === "terminal" ? "active" : ""}`}
          onClick={() => onSelectTab("terminal")}
          title="Output / Console"
          aria-label="Output"
        >
          <TerminalSquare size={18} />
          {activeTab === "terminal" && <span className="active-indicator" />}
        </button>

        {onToggleAgentPanel && (
          <button
            className={`activity-icon-btn agent-toggle ${agentPanelVisible ? "active" : ""}`}
            onClick={onToggleAgentPanel}
            title={agentPanelVisible ? "Hide ASEOS Agent (Ctrl+L)" : "Show ASEOS Agent (Ctrl+L)"}
            aria-label="Toggle ASEOS Agent"
          >
            <Bot size={18} />
            {agentPanelVisible && <span className="active-indicator" />}
          </button>
        )}
      </div>

      <div className="activity-bar-group bottom">
        <button
          className="activity-icon-btn"
          onClick={onOpenSettings}
          title="Manage Settings"
          aria-label="Settings"
        >
          <Settings size={18} />
        </button>
      </div>
    </aside>
  );
};

export default ActivityBar;
