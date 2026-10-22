import React, { useState } from "react";
import {
  X,
  Copy,
  Check,
  FileCode2,
  FileText,
  FileCog,
  FileCheck2,
  Sparkles,
  ChevronRight,
  WrapText,
  Eye,
} from "lucide-react";
import { highlightPythonLine, highlightMarkdownLine } from "../utils/syntaxHighlighter";

export type CodeEditorProps = {
  activeFile: string;
  content: string;
  openFiles?: string[];
  onSelectFile?: (file: string) => void;
  onCloseFile?: (file: string) => void;
  changedFiles?: string[];
};

function getFileIcon(fileName: string) {
  if (fileName.includes("test_") || fileName.includes("_test")) {
    return <FileCheck2 size={13} className="tab-icon test" />;
  }
  if (fileName.endsWith(".py")) {
    return <FileCode2 size={13} className="tab-icon python" />;
  }
  if (fileName.endsWith(".md")) {
    return <FileText size={13} className="tab-icon markdown" />;
  }
  if (fileName.endsWith(".toml") || fileName.endsWith(".json")) {
    return <FileCog size={13} className="tab-icon config" />;
  }
  return <FileText size={13} className="tab-icon generic" />;
}

export const CodeEditor: React.FC<CodeEditorProps> = ({
  activeFile,
  content,
  openFiles = ["src/main.py"],
  onSelectFile,
  onCloseFile,
  changedFiles = ["src/main.py"],
}) => {
  const [copied, setCopied] = useState(false);
  const [wrapLines, setWrapLines] = useState(false);

  const handleCopy = () => {
    if (!content) return;
    navigator.clipboard.writeText(content).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const lines = content ? content.split("\n") : [];
  const isPython = activeFile.endsWith(".py");
  const isMarkdown = activeFile.endsWith(".md");
  const isModified = changedFiles.includes(activeFile);

  // If no file is open/selected
  if (!activeFile || openFiles.length === 0) {
    return (
      <section className="editor-section empty">
        <div className="editor-empty-container">
          <div className="empty-watermark">
            <Sparkles size={48} className="watermark-icon" />
          </div>
          <h2 className="empty-title">No File Selected</h2>
          <p className="empty-subtitle">
            Choose a file from the Explorer or select a recent tab to view code.
          </p>

          <div className="shortcuts-cheatsheet">
            <div className="shortcut-row">
              <span className="shortcut-desc">Run Task</span>
              <kbd>Ctrl+Enter</kbd>
            </div>
            <div className="shortcut-row">
              <span className="shortcut-desc">Toggle Explorer</span>
              <kbd>Ctrl+B</kbd>
            </div>
            <div className="shortcut-row">
              <span className="shortcut-desc">Toggle Bottom Panel</span>
              <kbd>Ctrl+J</kbd>
            </div>
          </div>

          {onSelectFile && (
            <button
              className="open-sample-btn"
              onClick={() => onSelectFile("src/main.py")}
            >
              Open src/main.py
            </button>
          )}
        </div>
      </section>
    );
  }

  // Breadcrumb path parts
  const pathParts = activeFile.split("/");

  return (
    <section className="editor-section">
      {/* Editor Tab Bar */}
      <div className="editor-tabbar" role="tablist">
        <div className="editor-tabs-scroll">
          {openFiles.map((file) => {
            const fileName = file.split("/").pop() || file;
            const isActive = activeFile === file;
            const fileIsModified = changedFiles.includes(file);

            return (
              <div
                key={file}
                role="tab"
                aria-selected={isActive}
                className={`editor-tab ${isActive ? "active" : ""}`}
                onClick={() => onSelectFile && onSelectFile(file)}
                title={file}
              >
                {getFileIcon(file)}
                <span className="tab-title">{fileName}</span>
                {fileIsModified && (
                  <span className="tab-dirty-dot" title="Modified by agent" />
                )}
                {onCloseFile && (
                  <button
                    className="tab-close-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      onCloseFile(file);
                    }}
                    title="Close Tab"
                    aria-label={`Close ${fileName}`}
                  >
                    <X size={12} />
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* Tabbar controls */}
        <div className="editor-tabbar-actions">
          <button
            className={`tabbar-action-btn ${wrapLines ? "active" : ""}`}
            onClick={() => setWrapLines(!wrapLines)}
            title={wrapLines ? "Disable Word Wrap" : "Enable Word Wrap"}
          >
            <WrapText size={14} />
          </button>
          <button
            className="tabbar-action-btn"
            onClick={handleCopy}
            title={copied ? "Copied!" : "Copy File Content"}
          >
            {copied ? <Check size={14} className="copied-icon" /> : <Copy size={14} />}
          </button>
        </div>
      </div>

      {/* Breadcrumbs Bar */}
      <div className="editor-breadcrumbs">
        <div className="breadcrumb-path">
          <span className="breadcrumb-root">ASEOS</span>
          {pathParts.map((part, index) => (
            <React.Fragment key={index}>
              <ChevronRight size={11} className="breadcrumb-sep" />
              <span
                className={`breadcrumb-item ${
                  index === pathParts.length - 1 ? "current" : ""
                }`}
              >
                {part}
              </span>
            </React.Fragment>
          ))}
        </div>

        <div className="breadcrumb-meta">
          {isModified && (
            <span className="badge-modified-pill" title="File modified in this task">
              Modified
            </span>
          )}
          <span className="badge-readonly-pill">
            <Eye size={11} /> Read-only Preview
          </span>
          <span className="language-indicator">
            {isPython ? "Python" : isMarkdown ? "Markdown" : "Plain Text"}
          </span>
        </div>
      </div>

      {/* Code Area */}
      <div className={`editor-code-container ${wrapLines ? "wrap" : "nowrap"}`}>
        <div className="code-gutter" aria-hidden="true">
          {lines.map((_, index) => (
            <div key={index} className="gutter-line-number">
              {index + 1}
            </div>
          ))}
        </div>

        <div className="code-content">
          {lines.map((line, index) => {
            const isDiffLine =
              isModified &&
              line.includes('print("Hello from ASEOS")');

            return (
              <div
                key={index}
                className={`code-line-row ${isDiffLine ? "line-added-highlight" : ""}`}
              >
                <div className="code-line-text">
                  {isPython
                    ? highlightPythonLine(line)
                    : isMarkdown
                    ? highlightMarkdownLine(line)
                    : line || " "}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
};

export default CodeEditor;