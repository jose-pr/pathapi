# `github:`, `gitlab:` and `git:`

None needs a client library: they send through the same transport as `http:`.

::: pathlib_next.uri.schemes._gitrepo
    options:
      members:
        - DEFAULT_TIMEOUT
        - BaseRepoBackend
        - InsecureTransportWarning
        - RepoBackend
      show_root_heading: true

::: pathlib_next.uri.schemes._gitrepo._RepoApiPath
    options:
      show_if_no_docstring: true
      filters: ["!^_"]

::: pathlib_next.uri.schemes.github.GitHubPath

::: pathlib_next.uri.schemes.gitlab.GitLabPath

::: pathlib_next.uri.schemes.git._base.GitPath

::: pathlib_next.uri.schemes.git.github.GitHubGitPath

::: pathlib_next.uri.schemes.git.gitlab.GitLabGitPath
