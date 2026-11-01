const repository = "timmyagentic/agentic-fan";
const latest = `https://github.com/${repository}/releases/latest`;

async function currentDownload() {
  const response = await fetch(`https://api.github.com/repos/${repository}/releases/latest`, {
    headers: { Accept: "application/vnd.github+json", "User-Agent": "agentic-fan-website" },
    cf: { cacheEverything: true, cacheTtlByStatus: { "200": 300, "400-599": 0 } },
    signal: AbortSignal.timeout(4000),
  });
  if (!response.ok) return latest;
  const release = await response.json();
  const version = /^v(\d+\.\d+\.\d+)$/.exec(release.tag_name)?.[1];
  if (!version || release.draft || release.prerelease) return latest;

  const filename = `AgenticFan-${version}-arm64.dmg`;
  const url = `https://github.com/${repository}/releases/download/${release.tag_name}/${filename}`;
  return release.assets.some(asset => asset.state === "uploaded" && asset.browser_download_url === url)
    ? url
    : latest;
}

export default {
  async fetch(request, env) {
    if (new URL(request.url).pathname !== "/download") return env.ASSETS.fetch(request);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response(null, { status: 405, headers: { Allow: "GET, HEAD" } });
    }

    // A GitHub outage or a release without a compatible package still has a human download page.
    let destination = latest;
    try {
      destination = await currentDownload();
    } catch {
      // Keep the release page available when the metadata request fails.
    }
    return new Response(null, {
      status: 302,
      headers: { Location: destination, "Cache-Control": "no-store" },
    });
  },
};
