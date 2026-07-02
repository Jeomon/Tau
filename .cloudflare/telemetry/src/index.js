const VERSION_PATTERN = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/api/report-install") {
      return new Response("Not found", { status: 404 });
    }

    const contentLength = Number(request.headers.get("content-length") || "0");
    if (contentLength > 128) {
      return new Response("Payload too large", { status: 413 });
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return new Response("Invalid JSON", { status: 400 });
    }

    const version = payload?.version;
    if (typeof version !== "string" || !VERSION_PATTERN.test(version)) {
      return new Response("Invalid version", { status: 400 });
    }

    await env.DB.prepare(
      `INSERT INTO version_counts (version, count)
       VALUES (?, 1)
       ON CONFLICT(version) DO UPDATE SET count = count + 1`,
    )
      .bind(version)
      .run();

    return new Response(null, { status: 204 });
  },
};
