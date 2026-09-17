export type ExecutionResult = {
  action_id: string;
  success: boolean;
  stdout: string;
  stderr: string;
  exit_code: number | null;
  duration_ms: number | null;
};

export type TestRunResult = {
  status: "PASS" | "FAIL" | "TIMEOUT" | "ERROR";
  passed: number;
  failed: number;
};

export type TaskResult = {
  task_id: string;
  status: "COMPLETED" | "FAILED" | "ABORTED";
  summary: string;
  changed_files: string[];
  test_result: TestRunResult | null;
};