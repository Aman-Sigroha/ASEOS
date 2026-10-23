CAP_FS_READ = "fs.read"
CAP_FS_WRITE = "fs.write"
CAP_FS_DELETE = "fs.delete"
CAP_GIT_READ = "git.read"
CAP_GIT_WRITE = "git.write"
CAP_GIT_DISCARD = "git.discard"
CAP_WORKTREE_MANAGE = "worktree.manage"
CAP_WORKTREE_DISCARD = "worktree.discard"
CAP_TEST_RUN = "test.run"
CAP_SHELL_RUN = "shell.run"

# Capabilities granted by default. shell.run, fs.delete, git.write,
# git.discard, worktree.manage and worktree.discard are intentionally absent:
# they need an explicit grant.
DEFAULT_CAPABILITIES = frozenset(
    {CAP_FS_READ, CAP_FS_WRITE, CAP_GIT_READ, CAP_TEST_RUN}
)
