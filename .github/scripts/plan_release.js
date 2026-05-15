module.exports = async ({github, context, core}) => {

  const owner = context.repo.owner;
  const repo = context.repo.repo;
  const digest = process.env.UPSTREAM_JSON_DIGEST;
  const today = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  const datedVersionPattern = /^v?(\d+)\.(\d{8})\.(\d+)$/;
  const digestPattern = /Upstream JSON SHA256:\s*([a-f0-9]{64})/i;

  let latestDigest = "";
  let latestCommit = "";
  let latestReleaseTag = "";
  let latestMajor = 0;

  try {
    const release = await github.rest.repos.getLatestRelease({owner, repo});
    latestReleaseTag = release.data.tag_name;
    const releaseVersion = release.data.tag_name.match(datedVersionPattern);
    if (releaseVersion) {
      latestMajor = Number.parseInt(releaseVersion[1], 10);
    }
    latestDigest = release.data.body?.match(digestPattern)?.[1] ?? "";
  } catch (error) {
    if (error.status !== 404) {
      throw error;
    }
  }

  if (latestReleaseTag) {
    const ref = await github.rest.git.getRef({
      owner,
      repo,
      ref: `tags/${latestReleaseTag}`,
    });
    if (ref.data.object.type === "tag") {
      const tag = await github.rest.git.getTag({
        owner,
        repo,
        tag_sha: ref.data.object.sha,
      });
      latestCommit = tag.data.object.sha;
      latestDigest ||= tag.data.message.match(digestPattern)?.[1] ?? "";
    } else {
      latestCommit = ref.data.object.sha;
    }
  }

  const tags = await github.paginate(
    github.rest.repos.listTags,
    {owner, repo, per_page: 100},
  );

  if (latestMajor === 0) {
    for (const tag of tags) {
      const match = tag.name.match(datedVersionPattern);
      if (match) {
        latestMajor = Math.max(latestMajor, Number.parseInt(match[1], 10));
      }
    }
  }

  if (latestMajor === 0) {
    for (const tag of tags) {
      const match = tag.name.match(/^v?(\d+)\.(\d+)\.(\d+)$/);
      if (match) {
        latestMajor = Math.max(latestMajor, Number.parseInt(match[1], 10));
      }
    }
  }

  const major = latestMajor;
  const usedPatches = new Set();
  for (const tag of tags) {
    const match = tag.name.match(datedVersionPattern);
    if (!match) {
      continue;
    }
    if (Number.parseInt(match[1], 10) === major && match[2] === today) {
      usedPatches.add(Number.parseInt(match[3], 10));
    }
  }

  try {
    const response = await fetch("https://pypi.org/pypi/modelsdotdev/json");
    if (response.ok) {
      const data = await response.json();
      for (const version of Object.keys(data.releases ?? {})) {
        const match = version.match(datedVersionPattern);
        if (!match) {
          continue;
        }
        if (Number.parseInt(match[1], 10) === major && match[2] === today) {
          usedPatches.add(Number.parseInt(match[3], 10));
        }
      }
    }
  } catch (error) {
    core.warning(`Unable to inspect PyPI releases: ${error.message}`);
  }

  let patch = 0;
  while (usedPatches.has(patch)) {
    patch += 1;
  }

  const manuallyTriggered = context.eventName === "workflow_dispatch";
  const digestChanged = !latestDigest || digest.toLowerCase() !== latestDigest.toLowerCase();
  const commitChanged = !latestCommit || latestCommit !== context.sha;
  const changed = manuallyTriggered || digestChanged || commitChanged;
  const reasons = [];

  if (manuallyTriggered) {
    reasons.push("manual publish requested");
  }

  if (digestChanged) {
    reasons.push(
      latestDigest
        ? "upstream JSON digest differs"
        : "no previous upstream JSON digest found",
    );
  }
  if (commitChanged) {
    reasons.push(
      latestCommit
        ? "source commit differs"
        : "no previous source commit found",
    );
  }
  if (reasons.length === 0) {
    reasons.push("upstream JSON digest and source commit match");
  }

  const reason = reasons.join("; ");
  const version = `${major}.${today}.${patch}`;

  core.setOutput("changed", String(changed));
  core.setOutput("reason", reason);
  core.setOutput("version", version);
  core.setOutput("tag", `v${version}`);
};
