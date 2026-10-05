# repo-stats

A line-count snapshot of the git repo you run it in: total lines of code, largest files, language breakdown, test counts, and Markdown counted separately. Each run shows what changed since the last one.

```bash
cd my-repo && node /path/to/repo-stats.mjs
REPO_STATS_SKIP="vendor,old-copies" node /path/to/repo-stats.mjs
```

It counts only files git tracks or would track (respects `.gitignore`). It writes `.repo-stats.json` in the repo for the next comparison; add it to your `.gitignore`. Needs Node 18+ and git.
