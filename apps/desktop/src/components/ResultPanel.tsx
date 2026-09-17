import type { TaskResult } from "../types/execution";

type ResultPanelProps = {
  result: TaskResult | null;
};

function ResultPanel({ result }: ResultPanelProps) {
  return (
    <section className="result-panel">
      <div className="panel-title">
        RESULT
      </div>

      {!result ? (
        <div className="result-content">
          <div className="result-row">
            No task has been run yet.
          </div>
        </div>
      ) : (
        <div className="result-content">
          <div className="result-row">
            <strong>Status:</strong>{" "}
            {result.status}
          </div>

          <div className="result-row">
            <strong>Summary:</strong>{" "}
            {result.summary}
          </div>

          <div className="result-row">
            <strong>Changed files:</strong>{" "}
            {result.changed_files.length}
          </div>

          <div className="result-row">
            <strong>Tests:</strong>{" "}
            {result.test_result
              ? result.test_result.status
              : "Not run"}
          </div>
        </div>
      )}
    </section>
  );
}

export default ResultPanel;