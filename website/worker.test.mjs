import assert from "node:assert/strict";
import test from "node:test";
import worker from "./worker.mjs";

const base = "https://github.com/timmyagentic/agentic-fan/releases";
const asset = `${base}/download/v1.2.3/AgenticFan-1.2.3-arm64.dmg`;
const request = new Request("https://example.test/download");

test("selects the matching uploaded package from the current release", async t => {
  t.mock.method(globalThis, "fetch", async () => Response.json({
    tag_name: "v1.2.3",
    assets: [
      { state: "uploaded", browser_download_url: asset + ".sha256" },
      { state: "uploaded", browser_download_url: asset },
    ],
  }));
  const response = await worker.fetch(request, {});
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("location"), asset);
});

test("keeps the latest release page reachable when no package is available", async t => {
  const fetchMock = t.mock.method(globalThis, "fetch", async () => Response.json({ tag_name: "v1.2.3", assets: [] }));
  assert.equal((await worker.fetch(request, {})).headers.get("location"), base + "/latest");
  fetchMock.mock.mockImplementation(async () => { throw new Error("unavailable"); });
  assert.equal((await worker.fetch(request, {})).headers.get("location"), base + "/latest");
});
