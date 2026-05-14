module.exports = async ({ github, context, core }) => {
  const fs = require("fs");

  if (!context.ref.startsWith("refs/heads/")) {
    throw new Error(`version bump must run on a branch, got ${context.ref}`);
  }

  const owner = context.repo.owner;
  const repo = context.repo.repo;
  const branch = context.ref.replace(/^refs\/heads\//, "");
  const version = process.env.RELEASE_VERSION;

  const files = ["pyproject.toml", "uv.lock"];

  const base = await github.rest.git.getCommit({
    owner,
    repo,
    commit_sha: context.sha,
  });
  const treeEntries = [];
  for (const path of files) {
    const blob = await github.rest.git.createBlob({
      owner,
      repo,
      content: fs.readFileSync(path, "utf8"),
      encoding: "utf-8",
    });
    treeEntries.push({
      path,
      mode: "100644",
      type: "blob",
      sha: blob.data.sha,
    });
  }

  const tree = await github.rest.git.createTree({
    owner,
    repo,
    base_tree: base.data.tree.sha,
    tree: treeEntries,
  });
  const commit = await github.rest.git.createCommit({
    owner,
    repo,
    message: `Release ${version}`,
    tree: tree.data.sha,
    parents: [context.sha],
  });

  await github.rest.git.updateRef({
    owner,
    repo,
    ref: `heads/${branch}`,
    sha: commit.data.sha,
    force: false,
  });

  core.setOutput("sha", commit.data.sha);
};
