import React, { useState } from "react";
import { FolderGit2, X, Check, Search, Folder, Clock } from "lucide-react";

export type RepoModalProps = {
  isOpen: boolean;
  onClose: () => void;
  currentRepo: string;
  currentPath: string;
  onSelectRepo: (name: string, path: string) => void;
};

const RECENT_REPOS = [
  { name: "ASEOS", path: "C:\\Users\\ADMIN\\dev\\aseos\\ASEOS", branch: "feature/desktop-ui" },
  { name: "aseos-core", path: "C:\\Users\\ADMIN\\dev\\aseos-core", branch: "main" },
  { name: "aseos-runtime", path: "C:\\Users\\ADMIN\\dev\\aseos-runtime", branch: "develop" },
];

export const RepoModal: React.FC<RepoModalProps> = ({
  isOpen,
  onClose,
  currentRepo,
  currentPath,
  onSelectRepo,
}) => {
  const [search, setSearch] = useState("");
  const [customPath, setCustomPath] = useState(currentPath);

  if (!isOpen) return null;

  const filteredRepos = RECENT_REPOS.filter((r) =>
    r.name.toLowerCase().includes(search.toLowerCase()) ||
    r.path.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-content repo-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-row">
            <FolderGit2 size={16} className="modal-title-icon" />
            <h3>Switch Workspace Repository</h3>
          </div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close modal">
            <X size={15} />
          </button>
        </div>

        <div className="modal-body">
          <div className="repo-search-wrapper">
            <Search size={14} className="search-icon" />
            <input
              type="text"
              placeholder="Search recent repositories..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="repo-search-input"
              autoFocus
            />
          </div>

          <div className="repo-list-label">
            <Clock size={12} />
            <span>RECENT WORKSPACES</span>
          </div>

          <div className="recent-repos-list">
            {filteredRepos.map((repo) => {
              const isSelected = repo.name === currentRepo;
              return (
                <div
                  key={repo.name}
                  className={`repo-item ${isSelected ? "selected" : ""}`}
                  onClick={() => {
                    onSelectRepo(repo.name, repo.path);
                    onClose();
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <Folder size={16} className="repo-item-icon" />
                  <div className="repo-item-info">
                    <div className="repo-item-name-row">
                      <span className="repo-item-name">{repo.name}</span>
                      <span className="repo-item-branch">{repo.branch}</span>
                    </div>
                    <span className="repo-item-path">{repo.path}</span>
                  </div>
                  {isSelected && <Check size={14} className="repo-check-icon" />}
                </div>
              );
            })}
          </div>

          <div className="custom-path-section">
            <label className="custom-path-label">Or open local folder path:</label>
            <div className="custom-path-row">
              <input
                type="text"
                value={customPath}
                onChange={(e) => setCustomPath(e.target.value)}
                className="custom-path-input"
                placeholder="C:\path\to\repo"
              />
              <button
                className="custom-path-btn"
                onClick={() => {
                  if (customPath.trim()) {
                    const name = customPath.split(/[\\/]/).filter(Boolean).pop() || "project";
                    onSelectRepo(name, customPath);
                    onClose();
                  }
                }}
              >
                Open
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default RepoModal;
