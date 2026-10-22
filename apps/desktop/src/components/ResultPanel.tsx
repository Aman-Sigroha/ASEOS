import React, { useState } from "react";
import {
  CheckCircle2,
  XCircle,
  FlaskConical,
  RotateCw,
  Clock,
} from "lucide-react";
import type { TaskResult } from "../types/execution";

export type ResultPanelProps = {
  result: TaskResult | null;
  onInspectChanges?: () => void;
  onRerunTests?: () => void;
};

export const ResultPanel: React.FC<ResultPanelProps> = ({
  result,
  onInspectChanges,
  onRerunTests,
}) => {
  const [isRunningTests, setIsRunningTests] = useState(false);
  const [testOutputTab, setTestOutputTab] = useState<"summary" | "raw">("summary");

  const handleRerun = () => {
    setIsRunningTests(true);
    setTimeout(() => {
      setIsRunningTests(false);
      if (onRerunTests) onRerunTests();
    }, 800);
  };

  const testResult = result?.test_result || {
    status: "PASS",
    passed: 1,
    failed: 0,
  };

  const isSuccess = testResult.status === "PASS";

  return (
    <section className="test-results-panel" aria-label="Test Results">
      {/* Test Header */}
      <div className="test-header-bar">
        <div className="test-meta-left">
          <FlaskConical size={14} className="test-icon" />
          <span className="test-title">TEST SUITE RUNNER</span>
          <span
            className={`test-status-tag ${isSuccess ? "passed" : "failed"}`}
          >
            {isSuccess ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
            {testResult.status}
          </span>
          <span className="mock-pill">MOCK PYTEST</span>
        </div>

        <div className="test-toolbar-actions">
          <div className="test-view-toggle">
            <button
              className={`test-tab-btn ${testOutputTab === "summary" ? "active" : ""}`}
              onClick={() => setTestOutputTab("summary")}
            >
              Summary
            </button>
            <button
              className={`test-tab-btn ${testOutputTab === "raw" ? "active" : ""}`}
              onClick={() => setTestOutputTab("raw")}
            >
              Console Output
            </button>
          </div>

          <button
            className={`rerun-tests-btn ${isRunningTests ? "running" : ""}`}
            onClick={handleRerun}
            disabled={isRunningTests}
            title="Re-run pytest suite (Mock)"
          >
            <RotateCw size={12} className={isRunningTests ? "spin-icon" : ""} />
            <span>{isRunningTests ? "Running..." : "Re-run Tests"}</span>
          </button>
        </div>
      </div>

      {/* Test Content Area */}
      <div className="test-content-scroll">
        {testOutputTab === "summary" ? (
          <div className="test-summary-view">
            {/* Stat metric cards */}
            <div className="test-metric-cards">
              <div className="metric-card passed">
                <div className="metric-label">Passed</div>
                <div className="metric-value">{testResult.passed}</div>
                <div className="metric-sub">100% of collected</div>
              </div>

              <div className="metric-card failed">
                <div className="metric-label">Failed</div>
                <div className="metric-value">{testResult.failed}</div>
                <div className="metric-sub">No regressions</div>
              </div>

              <div className="metric-card duration">
                <div className="metric-label">Duration</div>
                <div className="metric-value">0.38s</div>
                <div className="metric-sub">pytest runner</div>
              </div>
            </div>

            {/* Test item breakdown */}
            <div className="test-breakdown-section">
              <div className="breakdown-header">
                <span>Executed Test Cases</span>
                <span className="breakdown-count">1 test</span>
              </div>

              <div className="test-case-row">
                <div className="test-case-info">
                  <CheckCircle2 size={13} className="test-case-status pass" />
                  <span className="test-case-path">tests/test_main.py</span>
                  <span className="test-case-name">::test_main</span>
                </div>
                <div className="test-case-meta">
                  <span className="test-duration">
                    <Clock size={11} /> 0.02s
                  </span>
                  <span className="badge-pass">PASSED</span>
                </div>
              </div>
            </div>

            {result?.changed_files && result.changed_files.length > 0 && onInspectChanges && (
              <div className="test-footer-notice">
                <span>Tests verified against modified files:</span>
                <button className="link-inspect-btn" onClick={onInspectChanges}>
                  Inspect {result.changed_files.join(", ")} diff
                </button>
              </div>
            )}
          </div>
        ) : (
          /* Console / Pytest Terminal Output */
          <div className="raw-test-console">
            <div className="console-line header">
              ============================= test session starts ==============================
            </div>
            <div className="console-line">
              platform win32 -- Python 3.11.8, pytest-8.1.1, pluggy-1.4.0
            </div>
            <div className="console-line">
              rootdir: C:\Users\ADMIN\dev\aseos\ASEOS
            </div>
            <div className="console-line">
              configfile: pyproject.toml
            </div>
            <div className="console-line">
              collected 1 item
            </div>
            <div className="console-line" />
            <div className="console-line test-pass-line">
              tests/test_main.py <span className="pass-dot">.</span> <span className="pass-percent">[100%]</span>
            </div>
            <div className="console-line" />
            <div className="console-line footer-pass">
              ============================== 1 passed in 0.38s ===============================
            </div>
          </div>
        )}
      </div>
    </section>
  );
};

export default ResultPanel;