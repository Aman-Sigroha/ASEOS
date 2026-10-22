import React, { useState } from "react";
import {
  Terminal as TerminalIcon,
  GitCompare,
  FlaskConical,
  X,
  Maximize2,
  Minimize2,
  Trash2,
  ChevronDown,
  CornerDownLeft,
} from "lucide-react";
import DiffViewer from "./DiffViewer";
import ResultPanel from "./ResultPanel";
import type { TaskResult } from "../types/execution";

export type BottomTab = "terminal" | "changes" | "tests";

export type BottomPanelProps = {
  activeTab: BottomTab;
  onSelectTab: (tab: BottomTab) => void;
  onClose: () => void;
  result: TaskResult | null;
  changedFiles?: string[];
  diffFile?: string;
  removedLine?: string;
  addedLine?: string;
  onDiscardMock?: () => void;
  isMaximized?: boolean;
  onToggleMaximize?: () => void;
};

export const BottomPanel: React.FC<BottomPanelProps> = ({
  activeTab,
  onSelectTab,
  onClose,
  result,
  changedFiles = ["src/main.py"],
  diffFile = "src/main.py",
  removedLine = 'print("Hello world")',
  addedLine = 'print("Hello from ASEOS")',
  onDiscardMock,
  isMaximized = false,
  onToggleMaximize,
}) => {
  const [terminalLines, setTerminalLines] = useState<string[]>([
    "ASEOS Developer Shell (Mock Execution Environment v0.1.0)",
    "Workspace: C:\\Users\\ADMIN\\dev\\aseos\\ASEOS",
    "Type 'help', 'git status', 'pytest', or 'clear' to test the mock shell.",
    "",
    "aseos@desktop:~/ASEOS$ git status",
    "On branch feature/desktop-ui",
    "Changes not staged for commit:",
    "  (use \"git add <file>...\" to update what will be committed)",
    "	modified:   src/main.py",
    "",
    "aseos@desktop:~/ASEOS$ pytest tests/",
    "============================= test session starts ==============================",
    "tests/test_main.py .                                                     [100%]",
    "============================== 1 passed in 0.38s ===============================",
  ]);

  const [cmdInput, setCmdInput] = useState("");

  const handleCommandSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const cmd = cmdInput.trim();
    if (!cmd) return;

    const newLines = [...terminalLines, `aseos@desktop:~/ASEOS$ ${cmd}`];

    if (cmd === "clear") {
      setTerminalLines([]);
      setCmdInput("");
      return;
    } else if (cmd === "help") {
      newLines.push(
        "Available mock commands: ls, git status, pytest, whoami, clear, pwd"
      );
    } else if (cmd === "ls" || cmd === "dir") {
      newLines.push("README.md   pyproject.toml   src/   tests/");
    } else if (cmd.startsWith("git status")) {
      newLines.push(
        "On branch feature/desktop-ui",
        "Changes not staged for commit:",
        "	modified:   src/main.py"
      );
    } else if (cmd.startsWith("pytest")) {
      newLines.push(
        "tests/test_main.py .                                                     [100%]",
        "============================== 1 passed in 0.35s ==============================="
      );
    } else if (cmd === "pwd") {
      newLines.push("C:\\Users\\ADMIN\\dev\\aseos\\ASEOS");
    } else if (cmd === "whoami") {
      newLines.push("aseos-engineer (local mock session)");
    } else {
      newLines.push(`bash: ${cmd}: command executed in mock sandbox`);
    }

    setTerminalLines(newLines);
    setCmdInput("");
  };

  return (
    <div className={`bottom-panel-wrapper ${isMaximized ? "maximized" : ""}`}>
      {/* Panel Tab Navigation Bar */}
      <div className="bottom-panel-nav">
        <div className="bottom-nav-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={activeTab === "terminal"}
            className={`bottom-tab-btn ${activeTab === "terminal" ? "active" : ""}`}
            onClick={() => onSelectTab("terminal")}
          >
            <TerminalIcon size={13} />
            <span>Terminal</span>
          </button>

          <button
            role="tab"
            aria-selected={activeTab === "changes"}
            className={`bottom-tab-btn ${activeTab === "changes" ? "active" : ""}`}
            onClick={() => onSelectTab("changes")}
          >
            <GitCompare size={13} />
            <span>Changes</span>
            {changedFiles.length > 0 && (
              <span className="tab-count-badge">{changedFiles.length}</span>
            )}
          </button>

          <button
            role="tab"
            aria-selected={activeTab === "tests"}
            className={`bottom-tab-btn ${activeTab === "tests" ? "active" : ""}`}
            onClick={() => onSelectTab("tests")}
          >
            <FlaskConical size={13} />
            <span>Tests</span>
            <span className="tab-count-badge pass">✓ 1</span>
          </button>
        </div>

        {/* Panel controls */}
        <div className="bottom-panel-controls">
          {activeTab === "terminal" && (
            <button
              className="panel-control-btn"
              onClick={() => setTerminalLines([])}
              title="Clear Terminal Output"
              aria-label="Clear Terminal"
            >
              <Trash2 size={13} />
            </button>
          )}

          {onToggleMaximize && (
            <button
              className="panel-control-btn"
              onClick={onToggleMaximize}
              title={isMaximized ? "Restore Panel Height" : "Maximize Panel Height"}
              aria-label={isMaximized ? "Restore Height" : "Maximize Height"}
            >
              {isMaximized ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
            </button>
          )}

          <button
            className="panel-control-btn"
            onClick={onClose}
            title="Collapse Bottom Panel (Ctrl+J)"
            aria-label="Collapse Panel"
          >
            <ChevronDown size={14} />
          </button>

          <button
            className="panel-control-btn close"
            onClick={onClose}
            title="Close Bottom Panel"
            aria-label="Close Panel"
          >
            <X size={13} />
          </button>
        </div>
      </div>

      {/* Panel Tab Content */}
      <div className="bottom-panel-body">
        {/* Terminal Tab */}
        {activeTab === "terminal" && (
          <div className="terminal-view-container">
            <div className="terminal-logs-scroll">
              {terminalLines.map((line, idx) => (
                <div
                  key={idx}
                  className={`terminal-line ${
                    line.startsWith("aseos@desktop")
                      ? "prompt"
                      : line.includes("passed")
                      ? "pass"
                      : line.includes("modified")
                      ? "mod"
                      : ""
                  }`}
                >
                  {line}
                </div>
              ))}
            </div>

            {/* Interactive Mock Command Line */}
            <form onSubmit={handleCommandSubmit} className="terminal-input-row">
              <span className="terminal-prompt-label">aseos@desktop:~/ASEOS$</span>
              <input
                type="text"
                value={cmdInput}
                onChange={(e) => setCmdInput(e.target.value)}
                placeholder="Type mock command (e.g. 'git status', 'pytest')..."
                className="terminal-cmd-input"
              />
              <CornerDownLeft size={11} className="terminal-enter-icon" />
            </form>
          </div>
        )}

        {/* Changes Tab */}
        {activeTab === "changes" && (
          <DiffViewer
            file={diffFile}
            removedLine={removedLine}
            addedLine={addedLine}
            onDiscardMock={onDiscardMock}
          />
        )}

        {/* Tests Tab */}
        {activeTab === "tests" && (
          <ResultPanel
            result={result}
            onInspectChanges={() => onSelectTab("changes")}
          />
        )}
      </div>
    </div>
  );
};

export default BottomPanel;
