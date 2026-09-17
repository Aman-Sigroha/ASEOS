type TaskInputProps = {
  task: string;
  isRunning: boolean;
  onTaskChange: (task: string) => void;
  onRunTask: () => void;
};

function TaskInput({
  task,
  isRunning,
  onTaskChange,
  onRunTask,
}: TaskInputProps) {
  const handleKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>
  ) => {
    if (event.key === "Enter") {
      onRunTask();
    }
  };

  return (
    <div className="task-bar">
      <input
        type="text"
        placeholder="Describe an engineering task..."
        value={task}
        disabled={isRunning}
        onChange={(event) =>
          onTaskChange(event.target.value)
        }
        onKeyDown={handleKeyDown}
      />

      <button
        className="run-button"
        disabled={isRunning || !task.trim()}
        onClick={onRunTask}
      >
        {isRunning ? "Running..." : "Run Task"}
      </button>
    </div>
  );
}

export default TaskInput;