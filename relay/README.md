# Report relay

Soundboard's **Report a bug** window sends reports here, and this worker
creates the GitHub issue. It exists so the app never carries a GitHub
token: anything shipped inside the app can be extracted from it.

Until a relay is deployed and `REPORT_URL` is set in `bug_report.py`,
Send falls back to opening GitHub's prefilled "new issue" page in the
browser and copying the report to the clipboard. That works without any
of this, but needs the reporter to have a GitHub account.

## Deploying

1. Create a fine-grained GitHub token: this repository only, permission
   **Issues: Read and write**, nothing else. Give it an expiry and a
   reminder to rotate it.
2. Create the `user-report` label on the repository.
3. From this folder, with a free Cloudflare account:

   ```
   npx wrangler kv namespace create REPORTS   # optional, for rate limiting
   npx wrangler secret put GITHUB_TOKEN       # paste the token
   npx wrangler deploy
   ```

   If you created the KV namespace, uncomment the `kv_namespaces` block in
   `wrangler.toml` and paste the id in first.
4. Put the deployed URL in `REPORT_URL` in `bug_report.py`, ending in the
   worker's hostname, and release a build.

The token never touches this repository or the app; only the worker holds
it, as a Cloudflare secret.

## What the worker does

- Accepts `POST` JSON with `title`, `body`, `error_hash` and `app_version`
  and nothing else.
- Rejects requests without an `X-Soundboard-Client` header, titles over
  120 characters and bodies over 32 KB.
- Rate limits to 5 reports per IP per hour when a KV namespace is bound.
- Dedupes: if an open `user-report` issue already carries the same error
  hash in its title, the report is added there as a comment instead of
  opening another issue.
- Creates the issue with the `user-report` and `bug` labels and returns
  its URL, which the app shows to the reporter.
