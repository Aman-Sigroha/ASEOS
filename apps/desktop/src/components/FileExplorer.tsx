type FileExplorerProps = {
  files: string[];
  activeFile: string;
  onFileSelect: (file: string) => void;
};

function FileExplorer({
  files,
  activeFile,
  onFileSelect,
}: FileExplorerProps) {
  return (
    <aside className="file-explorer">
      <div className="panel-title">FILES</div>

      {files.map((file) => (
        <div
          key={file}
          className={`file-item ${
            activeFile === file ? "active" : ""
          }`}
          onClick={() => onFileSelect(file)}
        >
          {file}
        </div>
      ))}
    </aside>
  );
}

export default FileExplorer;