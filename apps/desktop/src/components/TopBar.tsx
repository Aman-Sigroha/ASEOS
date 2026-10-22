import React from "react";
import {
  FolderGit2,
  GitBranch,
  Settings,
  PanelLeft,
  PanelBottom,
  PanelRight,
  ChevronDown,
  Sparkles,
  Command,
} from "lucide-react";

export type TopBarProps = {
  repoName: string;
  repoPath: string;
  onOpenRepository: () => void;
  branch?: string;
  sidebarVisible?: boolean;
  bottomPanelVisible?: boolean;
  agentPanelVisible?: boolean;
  onToggleSidebar?: () => void;
  onToggleBottomPanel?: () => void;
  onToggleAgentPanel?: () => void;
  onOpenSettings?: () => void;
};

export const TopBar: React.FC<TopBarProps> = ({
  repoName,
  repoPath,
  onOpenRepository,
  branch = "feature/desktop-ui",
  sidebarVisible = true,
  bottomPanelVisible = true,
  agentPanelVisible = true,
  onToggleSidebar,
  onToggleBottomPanel,
  onToggleAgentPanel,
  onOpenSettings,
}) => {
  return (
    <header className="topbar">
      {/* Brand & App Name */}
      <div className="topbar-section left">
        <div className="app-brand" title="ASEOS AI Coding Workspace">
          <div className="brand-logo-badge">
            <Sparkles className="brand-icon" size={15} />
          </div>
          <span className="brand-title">ASEOS</span>
          <span className="brand-badge">STUDIO</span>
        </div>

        {/* Repository selector */}
        <button
          className="repo-picker-button"
          onClick={onOpenRepository}
          title={`Active Workspace: ${repoPath} (Click to switch)`}
          aria-label="Switch repository"
        >
          <FolderGit2 size={14} className="repo-icon" />
          <span className="repo-name">{repoName}</span>
          <ChevronDown size={13} className="repo-chevron" />
        </button>

        {/* Git branch badge */}
        <div className="branch-badge" title={`Current Git branch: ${branch}`}>
          <GitBranch size={13} className="branch-icon" />
          <span className="branch-name">{branch}</span>
          <span className="branch-status-dot" />
        </div>
      </div>

      {/* Center command shortcut hint */}
      <div className="topbar-section center">
        <button
          className="command-palette-trigger"
          onClick={onOpenRepository}
          title="Command Palette (Ctrl + Shift + P)"
        >
          <Command size={12} />
          <span>Quick Open or Search</span>
          <kbd>Ctrl+P</kbd>
        </button>
      </div>

      {/* Right controls */}
      <div className="topbar-section right">
        {/* Layout controls */}
        <div className="layout-controls" role="group" aria-label="Layout toggles">
          <button
            className={`layout-btn ${sidebarVisible ? "active" : ""}`}
            onClick={onToggleSidebar}
            title={sidebarVisible ? "Hide Explorer (Ctrl+B)" : "Show Explorer (Ctrl+B)"}
            aria-label="Toggle Primary Sidebar"
          >
            <PanelLeft size={15} />
          </button>

          <button
            className={`layout-btn ${bottomPanelVisible ? "active" : ""}`}
            onClick={onToggleBottomPanel}
            title={bottomPanelVisible ? "Hide Terminal / Bottom Panel (Ctrl+J)" : "Show Terminal / Bottom Panel (Ctrl+J)"}
            aria-label="Toggle Bottom Panel"
          >
            <PanelBottom size={15} />
          </button>

          <button
            className={`layout-btn ${agentPanelVisible ? "active" : ""}`}
            onClick={onToggleAgentPanel}
            title={agentPanelVisible ? "Hide ASEOS Agent Panel" : "Show ASEOS Agent Panel"}
            aria-label="Toggle AI Agent Panel"
          >
            <PanelRight size={15} />
          </button>
        </div>

        <div className="topbar-divider" />

        {/* Settings button */}
        <button
          className="settings-button"
          onClick={onOpenSettings}
          title="Workspace Settings (Ctrl+,)"
          aria-label="Open Settings"
        >
          <Settings size={15} />
        </button>
      </div>
    </header>
  );
};

export default TopBar;