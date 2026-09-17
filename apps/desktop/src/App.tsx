import { useState } from "react";
import "./App.css";

import TopBar from "./components/TopBar";
import FileExplorer from "./components/FileExplorer";
import CodeEditor from "./components/CodeEditor";
import TaskInput from "./components/TaskInput";
import AgentActivity from "./components/AgentActivity";
import DiffViewer from "./components/DiffViewer";
import ResultPanel from "./components/ResultPanel";

import type { AgentEvent } from "./types/event";
import type { TaskResult } from "./types/execution";

function App() {
  const [task, setTask] = useState("");
  const [activeFile, setActiveFile] =
    useState("src/main.py");

  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [isRunning, setIsRunning] = useState(false);

  const [result, setResult] =
    useState<TaskResult | null>(null);

  const files = [
    "src/main.py",
    "src/utils.py",
    "src/config.py",
    "tests/test_main.py",
    "README.md",
  ];

  const fileContents: Record<string, string> = {
    "src/main.py": `def main():
    print("Hello from ASEOS")


if __name__ == "__main__":
    main()`,

    "src/utils.py": `def add(a, b):
    return a + b


def subtract(a, b):
    return a - b`,

    "src/config.py": `APP_NAME = "ASEOS"
VERSION = "0.1.0"
DEBUG = True`,

    "tests/test_main.py": `def test_main():
    assert True`,

    "README.md": `# My Project

A sample repository for ASEOS.`,
  };

  const runTask = async () => {
    if (!task.trim() || isRunning) {
      return;
    }

    setIsRunning(true);
    setEvents([]);
    setResult(null);

    const mockEvents: AgentEvent[] = [
      {
        event_id: "event-1",
        task_id: "task-001",
        event_type: "TASK_STARTED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: "Task received",
        data: {},
      },
      {
        event_id: "event-2",
        task_id: "task-001",
        event_type:
          "TASK_UNDERSTANDING_COMPLETED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: "Analyzing repository",
        data: {},
      },
      {
        event_id: "event-3",
        task_id: "task-001",
        event_type: "ACTION_STARTED",
        timestamp: new Date().toISOString(),
        action_id: "action-1",
        message: "Searching repository",
        data: {},
      },
      {
        event_id: "event-4",
        task_id: "task-001",
        event_type: "ACTION_COMPLETED",
        timestamp: new Date().toISOString(),
        action_id: "action-1",
        message: "Reading relevant files",
        data: {},
      },
      {
        event_id: "event-5",
        task_id: "task-001",
        event_type: "PLAN_CREATED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: "Creating engineering plan",
        data: {},
      },
      {
        event_id: "event-6",
        task_id: "task-001",
        event_type: "ACTION_STARTED",
        timestamp: new Date().toISOString(),
        action_id: "action-2",
        message: "Executing changes",
        data: {},
      },
      {
        event_id: "event-7",
        task_id: "task-001",
        event_type: "VERIFICATION_STARTED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: "Running tests",
        data: {},
      },
      {
        event_id: "event-8",
        task_id: "task-001",
        event_type: "TASK_COMPLETED",
        timestamp: new Date().toISOString(),
        action_id: null,
        message: "Task completed",
        data: {},
      },
    ];

    for (const event of mockEvents) {
      await new Promise((resolve) =>
        setTimeout(resolve, 700)
      );

      setEvents((previousEvents) => [
        ...previousEvents,
        event,
      ]);
    }

    setResult({
      task_id: "task-001",
      status: "COMPLETED",
      summary:
        "Task completed successfully.",
      changed_files: ["src/main.py"],
      test_result: {
        status: "PASS",
        passed: 1,
        failed: 0,
      },
    });

    setIsRunning(false);
  };

  return (
    <div className="app">

      <TopBar
        repoName="ASEOS"
        repoPath={
          "C:\\Users\\ADMIN\\dev\\aseos\\ASEOS"
        }
        onOpenRepository={() =>
          console.log("Open repository")
        }
      />

      <main className="workspace">

        <FileExplorer
          files={files}
          activeFile={activeFile}
          onFileSelect={setActiveFile}
        />

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            minWidth: 0,
            minHeight: 0,
          }}
        >
          <CodeEditor
            activeFile={activeFile}
            content={
              fileContents[activeFile] || ""
            }
          />

          <TaskInput
            task={task}
            isRunning={isRunning}
            onTaskChange={setTask}
            onRunTask={runTask}
          />
        </div>

        <AgentActivity
          events={events}
          isRunning={isRunning}
        />

      </main>

      <section className="bottom-panel">

        <DiffViewer
          file="src/main.py"
          removedLine={'print("Hello world")'}
          addedLine={'print("Hello from ASEOS")'}
        />

        <ResultPanel result={result} />

      </section>

    </div>
  );
}

export default App;