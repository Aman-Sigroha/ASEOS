type TopBarProps = {
  repoName: string;
  repoPath: string;
  onOpenRepository: () => void;
};

function TopBar({
  repoName,
  repoPath,
  onOpenRepository,
}: TopBarProps) {
  return (
    <header className="topbar">
      <div className="logo">ASEOS</div>

      <div className="repo-info">
        <div className="repo-name">{repoName}</div>
        <div className="repo-path">{repoPath}</div>
      </div>

      <button
        className="open-button"
        onClick={onOpenRepository}
      >
        Open Repository
      </button>
    </header>
  );
}

export default TopBar;