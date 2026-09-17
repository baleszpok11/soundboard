// Cloudflare Worker that turns a report from the app into a GitHub issue.
//
// The app can't hold a GitHub token: anything shipped inside it can be
// extracted. The token lives here as a secret instead, scoped to Issues on
// this one repository, so the worst a leak of this endpoint allows is
// unwanted issues, which are rate limited below.

const REPO = "baleszpok11/soundboard";
const LABELS = ["user-report", "bug"];
const LIMITS = { title: 120, body: 32 * 1024 };
const RATE_LIMIT = { count: 5, windowSeconds: 3600 };

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return json({ error: "POST only" }, 405);
    }
    if (!request.headers.get("X-Soundboard-Client")) {
      // Not security, just a filter for random traffic.
      return json({ error: "unknown client" }, 400);
    }

    let report;
    try {
      report = await request.json();
    } catch {
      return json({ error: "body must be JSON" }, 400);
    }

    const title = String(report.title || "").trim();
    const body = String(report.body || "").trim();
    if (!title || !body) {
      return json({ error: "title and body are required" }, 400);
    }
    if (title.length > LIMITS.title || body.length > LIMITS.body) {
      return json({ error: "report too long" }, 413);
    }

    const allowed = await underRateLimit(env, request);
    if (!allowed) {
      return json({ error: "too many reports from this address, try later" }, 429);
    }

    const hash = String(report.error_hash || "").slice(0, 16);
    const existing = hash ? await findExisting(env, hash) : null;
    if (existing) {
      await gh(env, `/repos/${REPO}/issues/${existing.number}/comments`, {
        body: `Another report of the same error:\n\n${body}`,
      });
      return json({ url: existing.html_url, deduped: true });
    }

    const issue = await gh(env, `/repos/${REPO}/issues`, { title, body, labels: LABELS });
    return json({ url: issue.html_url });
  },
};

async function underRateLimit(env, request) {
  if (!env.REPORTS) return true; // no KV bound, so no limiting
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const key = `rate:${ip}`;
  const count = Number((await env.REPORTS.get(key)) || 0);
  if (count >= RATE_LIMIT.count) return false;
  await env.REPORTS.put(key, String(count + 1), { expirationTtl: RATE_LIMIT.windowSeconds });
  return true;
}

async function findExisting(env, hash) {
  // The app puts the hash in the title, so the same crash lands on one issue.
  const query = encodeURIComponent(`repo:${REPO} is:issue is:open label:user-report in:title "${hash}"`);
  const found = await gh(env, `/search/issues?q=${query}`);
  return found.items && found.items.length ? found.items[0] : null;
}

async function gh(env, path, payload) {
  const response = await fetch(`https://api.github.com${path}`, {
    method: payload ? "POST" : "GET",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "soundboard-report-relay",
      "Content-Type": "application/json",
    },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  if (!response.ok) {
    throw new Error(`GitHub said ${response.status}`);
  }
  return response.json();
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
