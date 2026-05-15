module.exports = async ({ github, context, core }) => {
  const owner = context.repo.owner;
  const repo = context.repo.repo;
  const tag = process.env.RELEASE_TAG;
  const version = process.env.RELEASE_VERSION;
  const commit = process.env.RELEASE_COMMIT_SHA;
  const digest = process.env.UPSTREAM_JSON_DIGEST;

  try {
    await github.rest.git.getRef({ owner, repo, ref: `tags/${tag}` });
  } catch (error) {
    if (error.status !== 404) {
      throw error;
    }
    const annotatedTag = await github.rest.git.createTag({
      owner,
      repo,
      tag,
      message: `modelsdotdev ${version}\n\nUpstream JSON SHA256: ${digest}\n`,
      object: commit,
      type: "commit",
    });
    await github.rest.git.createRef({
      owner,
      repo,
      ref: `refs/tags/${tag}`,
      sha: annotatedTag.data.sha,
    });
  }

  const body = `modelsdotdev ${version}.`;

  try {
    await github.rest.repos.createRelease({
      owner,
      repo,
      tag_name: tag,
      name: tag,
      body,
    });
  } catch (error) {
    if (error.status !== 422) {
      throw error;
    }
    core.warning(`Release ${tag} already exists`);
  }
};
