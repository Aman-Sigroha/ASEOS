import React, { useState } from "react";
import {
  FileCode2,
  Copy,
  Check,
  Columns2,
  Rows3,
  RotateCcw,
} from "lucide-react";

export type DiffViewerProps = {
  file: string;
  removedLine: string;
  addedLine: string;
  onDiscardMock?: () => void;
};

export const DiffViewer: React.FC<DiffViewerProps> = ({
  file,
  removedLine,
  addedLine,
  onDiscardMock,
}) => {
  const [viewMode, setViewMode] = useState<"unified" | "split">("unified");
  const [copied, setCopied] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2000);
  };

  const handleCopy = () => {
    const rawDiff = `--- a/${file}\n+++ b/${file}\n@@ -1,5 +1,5 @@\n def main():\n-    ${removedLine}\n+    ${addedLine}\n \n if __name__ == "__main__":`;
    navigator.clipboard.writeText(rawDiff).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="diff-panel-container" aria-label="Diff Viewer">
      {toast && <div className="panel-toast">{toast}</div>}

      {/* Diff Header */}
      <div className="diff-header-bar">
        <div className="diff-file-meta">
          <FileCode2 size={14} className="file-icon python" />
          <span className="diff-filepath">{file}</span>
          <span className="diff-stat-add" title="1 line added">
            +1
          </span>
          <span className="diff-stat-del" title="1 line removed">
            -1
          </span>
          <span className="mock-pill">MOCK DIFF</span>
        </div>

        <div className="diff-toolbar-actions">
          {/* View Mode Toggle */}
          <div className="view-mode-toggle" role="group">
            <button
              className={`mode-btn ${viewMode === "unified" ? "active" : ""}`}
              onClick={() => setViewMode("unified")}
              title="Unified Diff View"
            >
              <Rows3 size={13} />
              <span>Unified</span>
            </button>
            <button
              className={`mode-btn ${viewMode === "split" ? "active" : ""}`}
              onClick={() => setViewMode("split")}
              title="Side-by-side Split View"
            >
              <Columns2 size={13} />
              <span>Split</span>
            </button>
          </div>

          <button
            className="diff-action-btn"
            onClick={handleCopy}
            title="Copy Raw Diff"
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
            <span>{copied ? "Copied" : "Copy"}</span>
          </button>

          <button
            className="diff-action-btn"
            onClick={() => {
              if (onDiscardMock) onDiscardMock();
              showToast("Changes reverted (Mock)");
            }}
            title="Discard changes"
          >
            <RotateCcw size={13} />
            <span>Discard</span>
          </button>
        </div>
      </div>

      {/* Diff Content View */}
      <div className="diff-scroll-body">
        {viewMode === "unified" ? (
          <div className="unified-diff-view">
            <div className="diff-chunk-header">
              <span className="chunk-symbol">@@</span>
              <span className="chunk-range">-1,5 +1,5</span>
              <span className="chunk-context">def main():</span>
            </div>

            {/* Context Line 1 */}
            <div className="diff-row context">
              <span className="diff-line-old">1</span>
              <span className="diff-line-new">1</span>
              <span className="diff-marker"> </span>
              <span className="diff-code">def main():</span>
            </div>

            {/* Removed Line */}
            <div className="diff-row deletion">
              <span className="diff-line-old">2</span>
              <span className="diff-line-new empty" />
              <span className="diff-marker">-</span>
              <span className="diff-code">    {removedLine}</span>
            </div>

            {/* Added Line */}
            <div className="diff-row addition">
              <span className="diff-line-old empty" />
              <span className="diff-line-new">2</span>
              <span className="diff-marker">+</span>
              <span className="diff-code">    {addedLine}</span>
            </div>

            {/* Context Line 3 */}
            <div className="diff-row context">
              <span className="diff-line-old">3</span>
              <span className="diff-line-new">3</span>
              <span className="diff-marker"> </span>
              <span className="diff-code" />
            </div>

            {/* Context Line 4 */}
            <div className="diff-row context">
              <span className="diff-line-old">4</span>
              <span className="diff-line-new">4</span>
              <span className="diff-marker"> </span>
              <span className="diff-code">if __name__ == "__main__":</span>
            </div>

            {/* Context Line 5 */}
            <div className="diff-row context">
              <span className="diff-line-old">5</span>
              <span className="diff-line-new">5</span>
              <span className="diff-marker"> </span>
              <span className="diff-code">    main()</span>
            </div>
          </div>
        ) : (
          /* Split / Side-by-Side View */
          <div className="split-diff-view">
            <div className="split-column original">
              <div className="split-col-header">Original (Base)</div>
              <div className="diff-row context">
                <span className="diff-line-old">1</span>
                <span className="diff-code">def main():</span>
              </div>
              <div className="diff-row deletion">
                <span className="diff-line-old">2</span>
                <span className="diff-code">    {removedLine}</span>
              </div>
              <div className="diff-row context">
                <span className="diff-line-old">3</span>
                <span className="diff-code" />
              </div>
              <div className="diff-row context">
                <span className="diff-line-old">4</span>
                <span className="diff-code">if __name__ == "__main__":</span>
              </div>
              <div className="diff-row context">
                <span className="diff-line-old">5</span>
                <span className="diff-code">    main()</span>
              </div>
            </div>

            <div className="split-column modified">
              <div className="split-col-header">Modified (Working Tree)</div>
              <div className="diff-row context">
                <span className="diff-line-new">1</span>
                <span className="diff-code">def main():</span>
              </div>
              <div className="diff-row addition">
                <span className="diff-line-new">2</span>
                <span className="diff-code">    {addedLine}</span>
              </div>
              <div className="diff-row context">
                <span className="diff-line-new">3</span>
                <span className="diff-code" />
              </div>
              <div className="diff-row context">
                <span className="diff-line-new">4</span>
                <span className="diff-code">if __name__ == "__main__":</span>
              </div>
              <div className="diff-row context">
                <span className="diff-line-new">5</span>
                <span className="diff-code">    main()</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
};

export default DiffViewer;