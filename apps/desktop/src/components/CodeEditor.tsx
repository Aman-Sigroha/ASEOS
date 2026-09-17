type CodeEditorProps = {
  activeFile: string;
  content: string;
};

function CodeEditor({
  activeFile,
  content,
}: CodeEditorProps) {
  const lines = content.split("\n");

  return (
    <section className="editor-section">
      <div className="editor-header">
        <span>{activeFile}</span>
      </div>

      <div className="editor">
        {lines.map((line, index) => (
          <div
            className="code-line"
            key={index}
          >
            <span className="line-number">
              {index + 1}
            </span>

            <span className="code">
              {line || " "}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

export default CodeEditor;