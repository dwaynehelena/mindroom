import readline from "node:readline";
import path from "node:path";
import { pathToFileURL } from "node:url";

const MAX_LINE_BYTES = 1024 * 1024;

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

async function dispatch(request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    throw new Error("request must be an object");
  }
  const { url, token, method, params, timeoutMs, packageRoot } = request;
  if (typeof url !== "string" || !url.startsWith("ws") || typeof token !== "string" || !token) {
    throw new Error("gateway url and token are required");
  }
  if (typeof method !== "string" || !method || !Number.isInteger(timeoutMs)) {
    throw new Error("method and integer timeoutMs are required");
  }
  if (typeof packageRoot !== "string" || !path.isAbsolute(packageRoot)) {
    throw new Error("absolute OpenClaw packageRoot is required");
  }
  const runtimeUrl = pathToFileURL(path.join(packageRoot, "dist", "plugin-sdk", "gateway-runtime.js"));
  const { GatewayClient } = await import(runtimeUrl.href);
  let client;
  try {
    const connected = new Promise((resolve, reject) => {
      client = new GatewayClient({
        url,
        token,
        role: "operator",
        scopes: ["operator.write"],
        requestTimeoutMs: timeoutMs,
        onHelloOk: resolve,
        onConnectError: reject,
      });
      client.start();
    });
    await Promise.race([
      connected,
      new Promise((_, reject) => setTimeout(() => reject(new Error("gateway connection timed out")), timeoutMs)),
    ]);
    return await client.request(method, params, { expectFinal: true, timeoutMs });
  } finally {
    if (client) await client.stopAndWait({ timeoutMs: 1000 }).catch(() => undefined);
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
let consumed = false;
for await (const line of input) {
  if (consumed) break;
  consumed = true;
  try {
    if (Buffer.byteLength(line, "utf8") > MAX_LINE_BYTES) throw new Error("request exceeds size limit");
    const result = await dispatch(JSON.parse(line));
    emit({ ok: true, result });
  } catch (error) {
    emit({ ok: false, error: error instanceof Error ? error.message : "gateway request failed" });
    process.exitCode = 1;
  }
}
