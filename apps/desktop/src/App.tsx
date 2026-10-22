import { useState, useEffect } from "react";
import "./App.css";

import { Bot } from "lucide-react";
import TopBar from "./components/TopBar";
import ActivityBar, { type SidebarTab } from "./components/ActivityBar";
import FileExplorer from "./components/FileExplorer";
import CodeEditor from "./components/CodeEditor";
import TaskInput from "./components/TaskInput";
import AgentActivity from "./components/AgentActivity";
import BottomPanel, { type BottomTab } from "./components/BottomPanel";
import StatusBar from "./components/StatusBar";
import RepoModal from "./components/RepoModal";
import SettingsModal from "./components/SettingsModal";

import type { AgentEvent } from "./types/event";
import type { TaskResult } from "./types/execution";

function App() {
  const [task, setTask] = useState("");
  const [submittedPrompt, setSubmittedPrompt] = useState("");
  const [activeFile, setActiveFile] = useState("src/main.py");
  const [openFiles, setOpenFiles] = useState<string[]>([
    "src/main.py",
    "src/utils.py",
  ]);

  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<TaskResult | null>(null);

  // Layout states
  const [sidebarVisible, setSidebarVisible] = useState(true);
  const [bottomPanelVisible, setBottomPanelVisible] = useState(true);
  const [agentPanelVisible, setAgentPanelVisible] = useState(true);
  const [bottomTab, setBottomTab] = useState<BottomTab>("terminal");
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("explorer");
  const [bottomPanelMaximized, setBottomPanelMaximized] = useState(false);

  // Modals & Settings
  const [repoModalOpen, setRepoModalOpen] = useState(false);
  const [settingsModalOpen, setSettingsModalOpen] = useState(false);
  const [repoName, setRepoName] = useState("ASEOS");
  const [repoPath, setRepoPath] = useState("C:\\Users\\ADMIN\\dev\\aseos\\ASEOS");
  const [selectedModel, setSelectedModel] = useState("claude-3-7-sonnet");
  const [stepDelayMs, setStepDelayMs] = useState(700);
  const [simulateFailure, setSimulateFailure] = useState(false);
  const [fontSize, setFontSize] = useState(14);

  const files = [
    "src/main.py",
    "src/utils.py",
    "src/config.py",
    "tests/test_main.py",
    "README.md",
    "pyproject.toml",
  ];

  const [changedFiles, setChangedFiles] = useState<string[]>(["src/main.py"]);

  const fileContents: Record<string, string> = {
    "src/main.py": `def main():
    print("Hello from ASEOS")


if __name__ == "__main__":
    main()`,

    "src/utils.py": `def add(a: int, b: int) -> int:
    """Return the sum of two numbers."""
    return a + b


def subtract(a: int, b: int) -> int:
    """Return the difference between two numbers."""
    return a - b`,

    "src/config.py": `APP_NAME = "ASEOS"
VERSION = "0.1.0"
DEBUG = True
WORKSPACE_ROOT = "C:/Users/ADMIN/dev/aseos/ASEOS"`,

    "tests/test_main.py": `def test_main():
    """Verify primary application entry point."""
    assert True


def test_utils():
    """Verify core math functions."""
    from src.utils import add, subtract
    assert add(2, 3) == 5
    assert subtract(5, 2) == 3`,

    "README.md": `# ASEOS — Autonomous Software Engineering OS

A next-generation AI coding workspace and orchestrator designed for precision engineering workflows.

## Architecture
- **Desktop UI**: Tauri + React + TypeScript
- **Agent Core**: Multi-step engineering planner
- **Test Runner**: Pytest execution sandbox

## Quick Start
1. Select a repository workspace.
2. Enter an engineering task in the prompt composer.
3. Review plan steps, live execution timeline, and diffs.`,

    "pyproject.toml": `[project]
name = "aseos"
version = "0.1.0"
description = "Autonomous Software Engineering Operating System"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "pytest>=8.0.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]`,
  };

  // Keyboard shortcut listeners (Ctrl+B, Ctrl+J, Ctrl+L, Ctrl+P, Ctrl+,)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isTyping =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);

      if (isTyping) {
        return;
      }

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        setSidebarVisible((prev) => !prev);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setBottomPanelVisible((prev) => !prev);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "l") {
        e.preventDefault();
        setAgentPanelVisible((prev) => !prev);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "p") {
        e.preventDefault();
        setRepoModalOpen(true);
      } else if ((e.ctrlKey || e.metaKey) && e.key === ",") {
        e.preventDefault();
        setSettingsModalOpen(true);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const handleOpenFile = (file: string) => {
    if (!openFiles.includes(file)) {
      setOpenFiles((prev) => [...prev, file]);
    }
    setActiveFile(file);
  };

  const handleCloseFile = (fileToClose: string) => {
    const remaining = openFiles.filter((f) => f !== fileToClose);
    setOpenFiles(remaining);
    if (activeFile === fileToClose) {
      if (remaining.length > 0) {
        setActiveFile(remaining[remaining.length - 1]);
      } else {
        setActiveFile("");
      }
    }
  };

  const runTask = async () => {
    if (!task.trim() || isRunning) {
      return;
    }

    const currentPrompt = task.trim();
    setSubmittedPrompt(currentPrompt);
    setIsRunning(true);
    setEvents([]);
    setResult(null);

    const normalEvents: AgentEvent[] = [
      {
        event_id: "event-1",
        task_id: "task-001",
        event_type: "TASK_STARTED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: "Task received and queued in ASEOS runtime",
        data: { prompt: currentPrompt },
      },
      {
        event_id: "event-2",
        task_id: "task-001",
        event_type: "TASK_UNDERSTANDING_COMPLETED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: "Analyzed repository structure and symbol dependencies",
        data: {},
      },
      {
        event_id: "event-3",
        task_id: "task-001",
        event_type: "ACTION_STARTED",
        timestamp: new Date().toISOString(),
        action_id: "action-1",
        message: "Searching workspace for target declarations",
        data: {},
      },
      {
        event_id: "event-4",
        task_id: "task-001",
        event_type: "ACTION_COMPLETED",
        timestamp: new Date().toISOString(),
        action_id: "action-1",
        message: "Indexed relevant source files (src/main.py, tests/test_main.py)",
        data: {},
      },
      {
        event_id: "event-5",
        task_id: "task-001",
        event_type: "PLAN_CREATED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: "Constructed engineering plan: 4 changes, 1 verification test",
        data: {},
      },
      {
        event_id: "event-6",
        task_id: "task-001",
        event_type: "ACTION_STARTED",
        timestamp: new Date().toISOString(),
        action_id: "action-2",
        message: "Applying edits to src/main.py",
        data: {},
      },
      {
        event_id: "event-7",
        task_id: "task-001",
        event_type: "VERIFICATION_STARTED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: "Running pytest verification suite on modified tree",
        data: {},
      },
      {
        event_id: "event-8",
        task_id: "task-001",
        event_type: simulateFailure ? "VERIFICATION_FAILED" : "TASK_COMPLETED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: simulateFailure
          ? "Verification failed: AssertionError in tests/test_main.py"
          : "Task successfully completed and verified",
        data: {},
      },
    ];

    for (const event of normalEvents) {
      await new Promise((resolve) => setTimeout(resolve, stepDelayMs));
      setEvents((previousEvents) => [...previousEvents, event]);
    }

    if (simulateFailure) {
      setResult({
        task_id: "task-001",
        status: "FAILED",
        summary:
          "Verification failed: 1 test assertion failed during regression check.",
        changed_files: ["src/main.py"],
        test_result: {
          status: "FAIL",
          passed: 0,
          failed: 1,
        },
      });
    } else {
      setResult({
        task_id: "task-001",
        status: "COMPLETED",
        summary: "Task completed successfully and all tests passed.",
        changed_files: ["src/main.py"],
        test_result: {
          status: "PASS",
          passed: 1,
          failed: 0,
        },
      });
      setChangedFiles(["src/main.py"]);
    }

    setIsRunning(false);
  };

  const handleResetSession = () => {
    setEvents([]);
    setResult(null);
    setSubmittedPrompt("");
    setTask("");
  };

  return (
    <div
      className="app"
      style={{
        ["--editor-font-size" as string]: `${fontSize}px`,
      }}
    >
      {/* Top Navigation Bar */}
      <TopBar
        repoName={repoName}
        repoPath={repoPath}
        branch="feature/desktop-ui"
        sidebarVisible={sidebarVisible}
        bottomPanelVisible={bottomPanelVisible}
        agentPanelVisible={agentPanelVisible}
        onToggleSidebar={() => setSidebarVisible(!sidebarVisible)}
        onToggleBottomPanel={() => setBottomPanelVisible(!bottomPanelVisible)}
        onToggleAgentPanel={() => setAgentPanelVisible(!agentPanelVisible)}
        onOpenRepository={() => setRepoModalOpen(true)}
        onOpenSettings={() => setSettingsModalOpen(true)}
      />

      {/* Main Workspace Layout */}
      <main className={`workspace-container ${agentPanelVisible ? "has-agent-open" : "agent-collapsed"}`}>
        {/* Far-left Activity Icon Bar */}
        <ActivityBar
          activeTab={sidebarTab}
          onSelectTab={(tab) => {
            if (sidebarTab === tab && sidebarVisible) {
              setSidebarVisible(false);
            } else {
              setSidebarTab(tab);
              setSidebarVisible(true);
            }
          }}
          onOpenSettings={() => setSettingsModalOpen(true)}
          gitChangeCount={changedFiles.length}
          agentPanelVisible={agentPanelVisible}
          onToggleAgentPanel={() => setAgentPanelVisible((prev) => !prev)}
        />

        {/* Collapsible Left Sidebar (File Explorer / Search / Git) */}
        {sidebarVisible && (
          <FileExplorer
            files={files}
            activeFile={activeFile}
            onFileSelect={handleOpenFile}
            openFiles={openFiles}
            onCloseFile={handleCloseFile}
            changedFiles={changedFiles}
            activeSidebarTab={sidebarTab}
            onSelectSidebarTab={setSidebarTab}
          />
        )}

        {/* Center Area (Editor + Bottom Panel) */}
        <div className="center-workspace-column">
          <div className="editor-and-composer-area">
            {/* Code Editor */}
            <CodeEditor
              activeFile={activeFile}
              content={fileContents[activeFile] || ""}
              openFiles={openFiles}
              onSelectFile={handleOpenFile}
              onCloseFile={handleCloseFile}
              changedFiles={changedFiles}
            />
          </div>

          {/* Bottom Panel (Terminal, Changes, Tests) */}
          {bottomPanelVisible && (
            <BottomPanel
              activeTab={bottomTab}
              onSelectTab={setBottomTab}
              onClose={() => setBottomPanelVisible(false)}
              result={result}
              changedFiles={changedFiles}
              diffFile="src/main.py"
              removedLine='print("Hello world")'
              addedLine='print("Hello from ASEOS")'
              isMaximized={bottomPanelMaximized}
              onToggleMaximize={() => setBottomPanelMaximized(!bottomPanelMaximized)}
              onDiscardMock={() => setChangedFiles([])}
            />
          )}
        </div>

        {/* Right AI Agent Panel (Kept mounted to preserve state & events) */}
        <div
          className={`right-agent-column ${!agentPanelVisible ? "collapsed" : ""}`}
          aria-hidden={!agentPanelVisible}
        >
          <AgentActivity
            events={events}
            isRunning={isRunning}
            taskPrompt={submittedPrompt}
            result={result}
            onInspectChanges={() => {
              setBottomPanelVisible(true);
              setBottomTab("changes");
            }}
            onInspectTests={() => {
              setBottomPanelVisible(true);
              setBottomTab("tests");
            }}
            onReset={handleResetSession}
            onSelectPrompt={(p) => setTask(p)}
            onOpenFile={handleOpenFile}
            onClosePanel={() => setAgentPanelVisible(false)}
          />

          {/* Multiline Task Composer */}
          <TaskInput
            task={task}
            isRunning={isRunning}
            onTaskChange={setTask}
            onRunTask={runTask}
            onSelectPrompt={(p) => setTask(p)}
            simulateFailure={simulateFailure}
            onToggleSimulateFailure={() => setSimulateFailure(!simulateFailure)}
          />
        </div>

        {/* When Agent Panel is collapsed, provide a visible, sleek reopen rail on the right edge */}
        {!agentPanelVisible && (
          <aside className="collapsed-agent-strip">
            <button
              className="reopen-agent-btn"
              onClick={() => setAgentPanelVisible(true)}
              title="Open ASEOS Agent Panel (Ctrl+L)"
              aria-label="Open ASEOS Agent Panel"
            >
              <Bot size={15} className="reopen-icon" />
              <span className="reopen-text">ASEOS Agent</span>
              {isRunning ? (
                <span className="reopen-badge running" title="Agent is executing...">
                  ●
                </span>
              ) : result ? (
                <span className="reopen-badge done" title="Task completed">
                  ✓
                </span>
              ) : null}
            </button>
          </aside>
        )}
      </main>

      {/* Bottom Status Bar */}
      <StatusBar
        branch="feature/desktop-ui"
        activeFile={activeFile}
        isRunning={isRunning}
        onToggleBottomPanel={() => setBottomPanelVisible(!bottomPanelVisible)}
        onToggleAgentPanel={() => setAgentPanelVisible((prev) => !prev)}
      />

      {/* Repo Switcher Modal */}
      <RepoModal
        isOpen={repoModalOpen}
        onClose={() => setRepoModalOpen(false)}
        currentRepo={repoName}
        currentPath={repoPath}
        onSelectRepo={(name, path) => {
          setRepoName(name);
          setRepoPath(path);
        }}
      />

      {/* Settings Modal */}
      <SettingsModal
        isOpen={settingsModalOpen}
        onClose={() => setSettingsModalOpen(false)}
        selectedModel={selectedModel}
        onSelectModel={setSelectedModel}
        stepDelayMs={stepDelayMs}
        onSelectStepDelay={setStepDelayMs}
        simulateFailure={simulateFailure}
        onToggleSimulateFailure={() => setSimulateFailure(!simulateFailure)}
        fontSize={fontSize}
        onSelectFontSize={setFontSize}
      />
    </div>
  );
}

export default App;