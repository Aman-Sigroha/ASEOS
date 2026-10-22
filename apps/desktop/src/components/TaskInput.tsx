import React, { useRef, useEffect } from "react";
import {
  Sparkles,
  CornerDownLeft,
  Loader2,
  FileCode,
  Flame,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";

export type TaskInputProps = {
  task: string;
  isRunning: boolean;
  onTaskChange: (task: string) => void;
  onRunTask: () => void;
  onSelectPrompt?: (prompt: string) => void;
  simulateFailure?: boolean;
  onToggleSimulateFailure?: () => void;
};

const SUGGESTED_PROMPTS = [
  "Update main.py to print greeting from ASEOS",
  "Refactor utils.py to include type annotations",
  "Add test cases for subtract in test_main.py",
];

export const TaskInput: React.FC<TaskInputProps> = ({
  task,
  isRunning,
  onTaskChange,
  onRunTask,
  onSelectPrompt,
  simulateFailure = false,
  onToggleSimulateFailure,
}) => {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea height
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(
        Math.max(textareaRef.current.scrollHeight, 72),
        160
      )}px`;
    }
  }, [task]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      if (!isRunning && task.trim()) {
        onRunTask();
      }
    }
  };

  return (
    <div className="task-composer-card">
      {/* Quick suggestions if empty and not running */}
      {!task.trim() && !isRunning && (
        <div className="prompt-suggestions">
          <div className="suggestions-header">
            <Flame size={12} className="suggestions-icon" />
            <span>Suggested tasks:</span>
          </div>
          <div className="suggestions-chips">
            {SUGGESTED_PROMPTS.map((prompt, idx) => (
              <button
                key={idx}
                type="button"
                className="suggestion-chip"
                onClick={() => {
                  if (onSelectPrompt) onSelectPrompt(prompt);
                  else onTaskChange(prompt);
                }}
              >
                {prompt}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Main Composer Box */}
      <div className={`composer-input-area ${isRunning ? "running" : ""}`}>
        <div className="composer-context-pill" title="Attached workspace context">
          <FileCode size={12} />
          <span>@ASEOS (5 files indexed)</span>
        </div>

        <textarea
          ref={textareaRef}
          value={task}
          disabled={isRunning}
          onChange={(e) => onTaskChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe an engineering task (e.g. 'Update main.py to print greeting from ASEOS')..."
          className="composer-textarea"
          rows={3}
        />

        {/* Composer bottom toolbar */}
        <div className="composer-toolbar">
          <div className="toolbar-left">
            <span className="shortcut-hint">
              <kbd>Ctrl</kbd> + <kbd>↵</kbd> to run
            </span>

            {onToggleSimulateFailure && (
              <label
                className="simulate-failure-toggle"
                title="Toggle between simulating a successful vs failed task"
              >
                <input
                  type="checkbox"
                  checked={simulateFailure}
                  onChange={onToggleSimulateFailure}
                  disabled={isRunning}
                />
                <span className="toggle-label">
                  {simulateFailure ? (
                    <span className="failure-text">
                      <AlertTriangle size={11} /> Simulate Failure
                    </span>
                  ) : (
                    <span className="success-text">
                      <CheckCircle2 size={11} /> Normal Flow
                    </span>
                  )}
                </span>
              </label>
            )}
          </div>

          <div className="toolbar-right">
            <button
              type="button"
              className={`composer-run-btn ${isRunning ? "loading" : ""}`}
              disabled={isRunning || !task.trim()}
              onClick={onRunTask}
              title={isRunning ? "Task is currently executing" : "Run Task (Ctrl+Enter)"}
            >
              {isRunning ? (
                <>
                  <Loader2 size={13} className="spin-icon" />
                  <span>Agent Working...</span>
                </>
              ) : (
                <>
                  <Sparkles size={13} />
                  <span>Run Task</span>
                  <CornerDownLeft size={11} className="enter-icon" />
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default TaskInput;