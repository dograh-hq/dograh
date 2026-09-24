// CLA check, run by .github/workflows/cla.yml through actions/github-script.
//
// Every commit author on a pull request must have signed CLA.md. Contributors
// sign by commenting SIGN_PHRASE on a pull request; the signature is appended
// to a JSON file on the orphan SIGNATURES_BRANCH, which is created on first
// use. The result is published as the STATUS_CONTEXT commit status, plus one
// PR comment (found again through COMMENT_MARKER) while anyone still needs to
// sign.
//
// Bump CLA_VERSION whenever CLA.md changes materially: signatures are stored
// per version, so everyone is asked to sign again.

const CLA_VERSION = 1;
const SIGN_PHRASE = 'I have read the CLA Document and I hereby sign the CLA';
const RECHECK_PHRASE = 'recheck';
const SIGNATURES_BRANCH = 'cla-signatures';
const SIGNATURES_PATH = `v${CLA_VERSION}/signatures.json`;
const STATUS_CONTEXT = 'license/cla';
const COMMENT_MARKER = '<!-- dograh-cla -->';
const MAX_WRITE_ATTEMPTS = 5;

// Maintainers with push access are covered by their agreements with Dograh.
// These are values of the legacy `permission` field, where maintain counts as
// write; `role_name` can be a custom role and is not checked.
const EXEMPT_PERMISSIONS = new Set(['admin', 'write']);

module.exports = async ({ github, context, core }) => {
  const { owner, repo } = context.repo;
  const repoRef = { github, owner, repo };
  const payload = context.payload;
  const prNumber = payload.pull_request?.number ?? payload.issue?.number;

  if (context.eventName === 'issue_comment') {
    if (!payload.issue.pull_request) return;
    const body = payload.comment.body.trim();
    if (body === SIGN_PHRASE && payload.comment.user.type !== 'Bot') {
      const recorded = await recordSignature(repoRef, payload.comment, prNumber);
      const login = payload.comment.user.login;
      core.info(recorded ? `Recorded CLA signature for @${login}` : `@${login} has already signed`);
    } else if (body !== RECHECK_PHRASE) {
      return;
    }
  }

  const { data: pr } = await github.rest.pulls.get({ owner, repo, pull_number: prNumber });
  if (pr.state !== 'open') return;

  const { signatures } = await readSignatures(repoRef);
  const signedIds = new Set(signatures.map((s) => s.id));

  const commits = await github.paginate(github.rest.pulls.listCommits, {
    owner,
    repo,
    pull_number: prNumber,
    per_page: 100,
  });

  // Co-authored-by trailers are not checked, only the commit author.
  const authors = new Map();
  const unlinked = [];
  for (const commit of commits) {
    if (commit.author) {
      authors.set(commit.author.id, commit.author);
    } else {
      // The author email is not linked to any GitHub account, so there is no
      // account whose signature could cover this commit.
      unlinked.push({ sha: commit.sha.slice(0, 7), name: commit.commit.author.name });
    }
  }

  const signed = [];
  const unsigned = [];
  for (const author of authors.values()) {
    if (author.type === 'Bot' || (await isMaintainer(repoRef, author.login))) continue;
    (signedIds.has(author.id) ? signed : unsigned).push(author.login);
  }

  const passing = unsigned.length === 0 && unlinked.length === 0;
  const claUrl = `https://github.com/${owner}/${repo}/blob/${payload.repository.default_branch}/CLA.md`;

  await github.rest.repos.createCommitStatus({
    owner,
    repo,
    sha: pr.head.sha,
    context: STATUS_CONTEXT,
    state: passing ? 'success' : 'failure',
    target_url: claUrl,
    description: passing
      ? 'All commit authors have signed the CLA'
      : describeFailure(unsigned.length, unlinked.length),
  });

  await upsertComment(repoRef, prNumber, {
    passing,
    body: passing
      ? passingComment(claUrl, signed)
      : failingComment({ owner, repo, claUrl, signed, unsigned, unlinked, payload }),
  });
};

async function isMaintainer({ github, owner, repo }, username) {
  try {
    const { data } = await github.rest.repos.getCollaboratorPermissionLevel({
      owner,
      repo,
      username,
    });
    return EXEMPT_PERMISSIONS.has(data.permission);
  } catch (error) {
    if (error.status === 404) return false;
    throw error;
  }
}

async function readSignatures({ github, owner, repo }) {
  try {
    const { data } = await github.rest.repos.getContent({
      owner,
      repo,
      path: SIGNATURES_PATH,
      ref: SIGNATURES_BRANCH,
    });
    const signatures = JSON.parse(Buffer.from(data.content, 'base64').toString('utf8'));
    return { signatures, sha: data.sha };
  } catch (error) {
    // Missing file or missing branch: nobody has signed this version yet.
    if (error.status === 404) return { signatures: [], sha: null };
    throw error;
  }
}

async function recordSignature(repoRef, comment, prNumber) {
  for (let attempt = 1; ; attempt++) {
    const { signatures, sha } = await readSignatures(repoRef);
    if (signatures.some((s) => s.id === comment.user.id)) return false;

    signatures.push({
      login: comment.user.login,
      id: comment.user.id,
      signed_at: comment.created_at,
      pull_request: prNumber,
      comment_id: comment.id,
      comment_url: comment.html_url,
    });

    try {
      await writeSignatures(
        repoRef,
        signatures,
        sha,
        `Record CLA signature for @${comment.user.login}`,
      );
      return true;
    } catch (error) {
      // 409: the file changed since it was read. 422: the branch or file was
      // created concurrently. Either way, re-read and append again.
      if (attempt >= MAX_WRITE_ATTEMPTS || ![409, 422].includes(error.status)) throw error;
    }
  }
}

async function writeSignatures({ github, owner, repo }, signatures, sha, message) {
  const content = `${JSON.stringify(signatures, null, 2)}\n`;

  if (sha === null && !(await branchExists({ github, owner, repo }, SIGNATURES_BRANCH))) {
    // An orphan branch keeps signature commits out of the code history.
    const { data: tree } = await github.rest.git.createTree({
      owner,
      repo,
      tree: [{ path: SIGNATURES_PATH, mode: '100644', type: 'blob', content }],
    });
    const { data: commit } = await github.rest.git.createCommit({
      owner,
      repo,
      message,
      tree: tree.sha,
      parents: [],
    });
    await github.rest.git.createRef({
      owner,
      repo,
      ref: `refs/heads/${SIGNATURES_BRANCH}`,
      sha: commit.sha,
    });
    return;
  }

  await github.rest.repos.createOrUpdateFileContents({
    owner,
    repo,
    branch: SIGNATURES_BRANCH,
    path: SIGNATURES_PATH,
    message,
    content: Buffer.from(content).toString('base64'),
    ...(sha ? { sha } : {}),
  });
}

async function branchExists({ github, owner, repo }, branch) {
  try {
    await github.rest.repos.getBranch({ owner, repo, branch });
    return true;
  } catch (error) {
    if (error.status === 404) return false;
    throw error;
  }
}

async function upsertComment({ github, owner, repo }, prNumber, { passing, body }) {
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number: prNumber,
    per_page: 100,
  });
  const existing = comments.find((c) => c.body?.includes(COMMENT_MARKER));

  if (existing) {
    if (existing.body !== body) {
      await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body });
    }
  } else if (!passing) {
    // Stay silent on PRs that pass without ever having failed.
    await github.rest.issues.createComment({ owner, repo, issue_number: prNumber, body });
  }
}

function describeFailure(unsignedCount, unlinkedCount) {
  const parts = [];
  if (unsignedCount) parts.push(`${unsignedCount} commit author(s) need to sign the CLA`);
  if (unlinkedCount) parts.push(`${unlinkedCount} commit(s) have no linked GitHub account`);
  return parts.join('; ');
}

function passingComment(claUrl, signed) {
  const list = signed.map((login) => `- [x] @${login}`).join('\n');
  return [
    COMMENT_MARKER,
    `All commit authors have signed the [Contributor License Agreement](${claUrl}). Thank you!`,
    ...(list ? ['', list] : []),
  ].join('\n');
}

function failingComment({ owner, repo, claUrl, signed, unsigned, unlinked, payload }) {
  const branch = payload.repository.default_branch;
  const corporateUrl = `https://github.com/${owner}/${repo}/blob/${branch}/CORPORATE_CLA.md`;
  const lines = [
    COMMENT_MARKER,
    `Thank you for your contribution! Before we can merge it, everyone who authored a commit in this pull request needs to sign our [Contributor License Agreement](${claUrl}). You keep the copyright to your work; the CLA lets Dograh accept and distribute it. You only sign once.`,
    '',
    'To sign, post a comment on this pull request with exactly this text:',
    '',
    '```',
    SIGN_PHRASE,
    '```',
  ];

  if (signed.length || unsigned.length) {
    lines.push('');
    for (const login of signed) lines.push(`- [x] @${login}`);
    for (const login of unsigned) lines.push(`- [ ] @${login}`);
  }

  if (unlinked.length) {
    lines.push(
      '',
      "These commits have an author email that isn't linked to a GitHub account, so we can't match them to a signature. Add the email to your GitHub account, or rewrite the commits with your GitHub email, then push again:",
      '',
      // Commit author names are free text; keep them inside inline code.
      ...unlinked.map(({ sha, name }) => `- \`${sha}\` by \`${name.replace(/`/g, "'")}\``),
    );
  }

  lines.push(
    '',
    `If your employer has rights to your work, see the [Corporate CLA](${corporateUrl}).`,
    '',
    `<sub>Comment \`${RECHECK_PHRASE}\` to run this check again.</sub>`,
  );
  return lines.join('\n');
}
