CAP_FS_READ = "fs.read"
CAP_FS_WRITE = "fs.write"
CAP_FS_DELETE = "fs.delete"
CAP_GIT_READ = "git.read"
CAP_TEST_RUN = "test.run"
CAP_SHELL_RUN = "shell.run"

# Capabilities granted by default. shell.run and fs.delete are intentionally
# absent: they need an explicit grant.
DEFAULT_CAPABILITIES = frozenset(
    {CAP_FS_READ, CAP_FS_WRITE, CAP_GIT_READ, CAP_TEST_RUN}
)
