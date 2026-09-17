type DiffViewerProps = {
  file: string;
  removedLine: string;
  addedLine: string;
};

function DiffViewer({
  file,
  removedLine,
  addedLine,
}: DiffViewerProps) {
  return (
    <section className="diff-panel">
      <div className="panel-title">
        DIFF — {file}
      </div>

      <div className="diff-content">
        <div className="diff-removed">
          - {removedLine}
        </div>

        <div className="diff-added">
          + {addedLine}
        </div>
      </div>
    </section>
  );
}

export default DiffViewer;