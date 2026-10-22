import React, { useState, useMemo } from "react";
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FolderOpen,
  FileCode2,
  FileText,
  FileCheck2,
  FileCog,
  Search,
  X,
  FilePlus2,
  FolderPlus,
  RefreshCw,
  Check,
} from "lucide-react";
import type { SidebarTab } from "./ActivityBar";

export type FileExplorerProps = {
  files: string[];
  activeFile: string;
  onFileSelect: (file: string) => void;
  openFiles?: string[];
  onCloseFile?: (file: string) => void;
  changedFiles?: string[];
  activeSidebarTab?: SidebarTab;
  onSelectSidebarTab?: (tab: SidebarTab) => void;
};

// Helper to determine file icon and color
function getFileDetails(fileName: string) {
  if (fileName.includes("test_") || fileName.includes("_test")) {
    return {
      icon: <FileCheck2 size={14} className="file-icon test" />,
      type: "test",
    };
  }
  if (fileName.endsWith(".py")) {
    return {
      icon: <FileCode2 size={14} className="file-icon python" />,
      type: "python",
    };
  }
  if (fileName.endsWith(".md")) {
    return {
      icon: <FileText size={14} className="file-icon markdown" />,
      type: "markdown",
    };
  }
  if (fileName.endsWith(".toml") || fileName.endsWith(".json") || fileName.endsWith(".yaml")) {
    return {
      icon: <FileCog size={14} className="file-icon config" />,
      type: "config",
    };
  }
  return {
    icon: <FileText size={14} className="file-icon generic" />,
    type: "generic",
  };
}

export const FileExplorer: React.FC<FileExplorerProps> = ({
  files,
  activeFile,
  onFileSelect,
  openFiles = [],
  onCloseFile,
  changedFiles = ["src/main.py"],
  activeSidebarTab = "explorer",
}) => {
  const [searchFilter, setSearchFilter] = useState("");
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({
    src: true,
    tests: true,
  });
  const [openEditorsCollapsed, setOpenEditorsCollapsed] = useState(false);
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(false);
  const [feedbackToast, setFeedbackToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setFeedbackToast(msg);
    setTimeout(() => setFeedbackToast(null), 2500);
  };

  const toggleFolder = (folderName: string) => {
    setExpandedFolders((prev) => ({
      ...prev,
      [folderName]: !prev[folderName],
    }));
  };

  // Group files into folder tree
  const tree = useMemo(() => {
    const folders: Record<string, string[]> = {};
    const rootFiles: string[] = [];

    const filtered = searchFilter.trim()
      ? files.filter((f) => f.toLowerCase().includes(searchFilter.toLowerCase()))
      : files;

    filtered.forEach((file) => {
      const parts = file.split("/");
      if (parts.length > 1) {
        const folder = parts[0];
        if (!folders[folder]) folders[folder] = [];
        folders[folder].push(file);
      } else {
        rootFiles.push(file);
      }
    });

    return { folders, rootFiles };
  }, [files, searchFilter]);

  // View: Search in workspace
  if (activeSidebarTab === "search") {
    return (
      <aside className="sidebar-panel">
        <div className="sidebar-header">
          <span className="sidebar-title">SEARCH WORKSPACE</span>
        </div>
        <div className="sidebar-search-box">
          <div className="search-input-wrapper">
            <Search size={13} className="search-icon" />
            <input
              type="text"
              placeholder="Search in files..."
              value={searchFilter}
              onChange={(e) => setSearchFilter(e.target.value)}
              className="sidebar-search-input"
            />
            {searchFilter && (
              <button
                className="clear-search-btn"
                onClick={() => setSearchFilter("")}
                title="Clear filter"
              >
                <X size={12} />
              </button>
            )}
          </div>
        </div>

        <div className="search-results-list">
          {searchFilter ? (
            <div>
              <div className="search-results-meta">
                Matching files ({files.filter((f) => f.toLowerCase().includes(searchFilter.toLowerCase())).length})
              </div>
              {files
                .filter((f) => f.toLowerCase().includes(searchFilter.toLowerCase()))
                .map((file) => (
                  <div
                    key={file}
                    className={`file-tree-item ${activeFile === file ? "active" : ""}`}
                    onClick={() => onFileSelect(file)}
                  >
                    {getFileDetails(file).icon}
                    <span className="file-name">{file}</span>
                  </div>
                ))}
            </div>
          ) : (
            <div className="sidebar-empty-state">
              <span>Type a term to search across workspace files.</span>
            </div>
          )}
        </div>
      </aside>
    );
  }

  // View: Source Control
  if (activeSidebarTab === "git") {
    return (
      <aside className="sidebar-panel">
        <div className="sidebar-header">
          <span className="sidebar-title">SOURCE CONTROL</span>
          <button
            className="sidebar-tool-btn"
            onClick={() => showToast("Git status refreshed")}
            title="Refresh status"
          >
            <RefreshCw size={13} />
          </button>
        </div>

        <div className="git-view-content">
          <div className="git-section-header">
            <div className="git-section-title">CHANGES ({changedFiles.length})</div>
          </div>

          <div className="git-changes-list">
            {changedFiles.map((f) => (
              <div
                key={f}
                className={`git-change-item ${activeFile === f ? "active" : ""}`}
                onClick={() => onFileSelect(f)}
              >
                <span className="git-status-char modified" title="Modified">
                  M
                </span>
                <span className="git-change-file">{f}</span>
              </div>
            ))}
          </div>

          <div className="git-commit-box">
            <textarea
              className="git-commit-input"
              placeholder="Commit message (Enter to commit)..."
              rows={2}
            />
            <button
              className="git-commit-btn"
              onClick={() => showToast("Changes committed locally (mock)")}
            >
              <Check size={13} />
              <span>Commit Changes</span>
            </button>
          </div>
        </div>
      </aside>
    );
  }

  // View: File Explorer (Default)
  return (
    <aside className="sidebar-panel">
      {/* Toast notification */}
      {feedbackToast && <div className="panel-toast">{feedbackToast}</div>}

      {/* Explorer Header */}
      <div className="sidebar-header">
        <span className="sidebar-title">EXPLORER</span>
        <div className="sidebar-header-actions">
          <button
            className="sidebar-tool-btn"
            onClick={() => showToast("Mock file created")}
            title="New File (Mock)"
          >
            <FilePlus2 size={13} />
          </button>
          <button
            className="sidebar-tool-btn"
            onClick={() => showToast("Mock folder created")}
            title="New Folder (Mock)"
          >
            <FolderPlus size={13} />
          </button>
          <button
            className="sidebar-tool-btn"
            onClick={() => {
              setExpandedFolders({ src: true, tests: true });
              showToast("Workspace refreshed");
            }}
            title="Refresh Explorer"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {/* Search Filter Input */}
      <div className="sidebar-search-box">
        <div className="search-input-wrapper">
          <Search size={13} className="search-icon" />
          <input
            type="text"
            placeholder="Filter files..."
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            className="sidebar-search-input"
          />
          {searchFilter && (
            <button
              className="clear-search-btn"
              onClick={() => setSearchFilter("")}
              title="Clear filter"
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      <div className="file-tree-container">
        {/* Open Editors Section */}
        {openFiles.length > 0 && (
          <div className="tree-section">
            <div
              className="tree-section-header"
              onClick={() => setOpenEditorsCollapsed(!openEditorsCollapsed)}
            >
              {openEditorsCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
              <span className="section-label">OPEN EDITORS</span>
            </div>

            {!openEditorsCollapsed && (
              <div className="open-editors-list">
                {openFiles.map((file) => {
                  const fileName = file.split("/").pop() || file;
                  const isModified = changedFiles.includes(file);
                  return (
                    <div
                      key={file}
                      className={`file-tree-item ${activeFile === file ? "active" : ""}`}
                      onClick={() => onFileSelect(file)}
                    >
                      {getFileDetails(file).icon}
                      <span className="file-name">{fileName}</span>
                      <span className="file-parent-path">{file.includes("/") ? file.split("/")[0] : ""}</span>
                      {isModified && <span className="dirty-indicator" title="Modified" />}
                      {onCloseFile && (
                        <button
                          className="close-item-btn"
                          onClick={(e) => {
                            e.stopPropagation();
                            onCloseFile(file);
                          }}
                          title="Close"
                        >
                          <X size={11} />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Workspace Root Folder */}
        <div className="tree-section">
          <div
            className="tree-section-header"
            onClick={() => setWorkspaceCollapsed(!workspaceCollapsed)}
          >
            {workspaceCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
            <span className="section-label root-label">ASEOS</span>
            <span className="badge-count">{files.length}</span>
          </div>

          {!workspaceCollapsed && (
            <div className="tree-content">
              {/* Folder entries */}
              {Object.entries(tree.folders).map(([folderName, folderFiles]) => {
                const isExpanded = expandedFolders[folderName] ?? true;
                return (
                  <div key={folderName} className="tree-folder-group">
                    <div
                      className="tree-folder-row"
                      onClick={() => toggleFolder(folderName)}
                      role="button"
                      tabIndex={0}
                    >
                      <span className="chevron-icon">
                        {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      </span>
                      <span className="folder-icon">
                        {isExpanded ? (
                          <FolderOpen size={14} className="folder-svg open" />
                        ) : (
                          <Folder size={14} className="folder-svg" />
                        )}
                      </span>
                      <span className="folder-name">{folderName}</span>
                      <span className="folder-count">{folderFiles.length}</span>
                    </div>

                    {isExpanded && (
                      <div className="tree-folder-children">
                        {folderFiles.map((file) => {
                          const baseName = file.split("/").slice(1).join("/");
                          const isModified = changedFiles.includes(file);
                          const isActive = activeFile === file;

                          return (
                            <div
                              key={file}
                              className={`file-tree-item ${isActive ? "active" : ""}`}
                              onClick={() => onFileSelect(file)}
                              role="button"
                              tabIndex={0}
                            >
                              {getFileDetails(file).icon}
                              <span className="file-name">{baseName}</span>
                              {isModified && (
                                <span className="git-status-tag" title="Modified">
                                  M
                                </span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Root files */}
              {tree.rootFiles.map((file) => {
                const isModified = changedFiles.includes(file);
                const isActive = activeFile === file;

                return (
                  <div
                    key={file}
                    className={`file-tree-item root-file ${isActive ? "active" : ""}`}
                    onClick={() => onFileSelect(file)}
                    role="button"
                    tabIndex={0}
                  >
                    {getFileDetails(file).icon}
                    <span className="file-name">{file}</span>
                    {isModified && (
                      <span className="git-status-tag" title="Modified">
                        M
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </aside>
  );
};

export default FileExplorer;