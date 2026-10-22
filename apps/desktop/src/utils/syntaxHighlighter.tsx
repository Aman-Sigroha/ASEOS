import React from "react";

// Token patterns for Python syntax highlighting
const PYTHON_KEYWORDS = new Set([
  "and", "as", "assert", "async", "await", "break", "class", "continue",
  "def", "del", "elif", "else", "except", "finally", "for", "from",
  "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
  "or", "pass", "raise", "return", "try", "while", "with", "yield"
]);

const PYTHON_BUILTINS = new Set([
  "print", "len", "range", "int", "str", "float", "bool", "list",
  "dict", "set", "tuple", "open", "type", "sum", "min", "max",
  "enumerate", "zip", "isinstance", "super", "self", "True", "False", "None"
]);

export function highlightPythonLine(line: string): React.ReactNode[] {
  if (!line) {
    return [<span key="empty">{" "}</span>];
  }

  const elements: React.ReactNode[] = [];
  let keyIndex = 0;

  // Regex tokens
  const tokenRegex = /(\s+|#.*$|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b\d+(?:\.\d+)?\b|\b[a-zA-Z_]\w*\b|[=+\-*/%<>!&|^~]+|[()[\]{},:;])/g;
  let match: RegExpExecArray | null;
  let lastIndex = 0;

  while ((match = tokenRegex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      elements.push(
        <span key={keyIndex++} className="token-plain">
          {line.slice(lastIndex, match.index)}
        </span>
      );
    }

    const token = match[0];
    lastIndex = tokenRegex.lastIndex;

    if (token.startsWith("#")) {
      elements.push(
        <span key={keyIndex++} className="token-comment">
          {token}
        </span>
      );
    } else if (token.startsWith('"') || token.startsWith("'")) {
      elements.push(
        <span key={keyIndex++} className="token-string">
          {token}
        </span>
      );
    } else if (/^\d+(?:\.\d+)?$/.test(token)) {
      elements.push(
        <span key={keyIndex++} className="token-number">
          {token}
        </span>
      );
    } else if (PYTHON_KEYWORDS.has(token)) {
      elements.push(
        <span key={keyIndex++} className="token-keyword">
          {token}
        </span>
      );
    } else if (PYTHON_BUILTINS.has(token)) {
      elements.push(
        <span key={keyIndex++} className="token-builtin">
          {token}
        </span>
      );
    } else if (line.slice(lastIndex).trimStart().startsWith("(")) {
      // Function call or definition
      elements.push(
        <span key={keyIndex++} className="token-function">
          {token}
        </span>
      );
    } else if (/^[=+\-*/%<>!&|^~]+$/.test(token)) {
      elements.push(
        <span key={keyIndex++} className="token-operator">
          {token}
        </span>
      );
    } else if (/^[()[\]{},:;]$/.test(token)) {
      elements.push(
        <span key={keyIndex++} className="token-punctuation">
          {token}
        </span>
      );
    } else {
      elements.push(
        <span key={keyIndex++} className="token-plain">
          {token}
        </span>
      );
    }
  }

  if (lastIndex < line.length) {
    elements.push(
      <span key={keyIndex++} className="token-plain">
        {line.slice(lastIndex)}
      </span>
    );
  }

  return elements.length > 0 ? elements : [<span key="empty">{" "}</span>];
}

export function highlightMarkdownLine(line: string): React.ReactNode[] {
  if (line.startsWith("#")) {
    return [<span key="h" className="token-heading">{line}</span>];
  }
  if (line.startsWith("- ") || line.startsWith("* ")) {
    return [
      <span key="bullet" className="token-bullet">{line.slice(0, 2)}</span>,
      <span key="text" className="token-plain">{line.slice(2)}</span>
    ];
  }
  return [<span key="text" className="token-plain">{line || " "}</span>];
}
