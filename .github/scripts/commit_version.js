module.exports = async ({ github, context, core }) => {
  const fs = require("fs");

  if (!context.ref.startsWith("refs/heads/")) {
    throw new Error(`version bump must run on a branch, got ${context.ref}`);
  }

  const owner = context.repo.owner;
  const repo = context.repo.repo;
  const branch = context.ref.replace(/^refs\/heads\//, "");
  const version = process.env.RELEASE_VERSION;
  const pyproject = fs.readFileSync("pyproject.toml", "utf8");

  const base = await github.rest.git.getCommit({
    owner,
    repo,
    commit_sha: context.sha,
  });
  const blob = await github.rest.git.createBlob({
    owner,
    repo,
    content: pyproject,
    encoding: "utf-8",
  });
  const tree = await github.rest.git.createTree({
    owner,
    repo,
    base_tree: base.data.tree.sha,
    tree: [
      {
        path: "pyproject.toml",
        mode: "100644",
        type: "blob",
        sha: blob.data.sha,
      },
    ],
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
