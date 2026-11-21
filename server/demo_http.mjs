// Local stand-in for the worker demo route.
// POST a one-page PDF, get a translated DOCX back.

import http from "http";
import { readFileSync } from "fs";
import { translateDemoPdf } from "./demo_page.js";

function loadEnv() {
  const env = {};
  const text = readFileSync(new URL("../.env", import.meta.url), "utf8");
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const split = trimmed.indexOf("=");
    const key = trimmed.slice(0, split).trim();
    const value = trimmed.slice(split + 1).trim().replace(/^["']|["']$/g, "");
    env[key] = value;
  }
  return env;
}

function parts(buffer, boundary) {
  const marker = Buffer.from(`--${boundary}`);
  const found = [];
  let start = buffer.indexOf(marker);
  while (start !== -1) {
    const next = buffer.indexOf(marker, start + marker.length);
    if (next === -1) break;
    let body = buffer.subarray(start + marker.length, next);
    if (body.subarray(0, 2).toString() === "\r\n") body = body.subarray(2);
    if (body.subarray(body.length - 2).toString() === "\r\n") body = body.subarray(0, body.length - 2);
    const split = body.indexOf("\r\n\r\n");
    const head = body.subarray(0, split).toString("utf8");
    const name = /name="([^"]+)"/.exec(head);
    found.push({
      name: name ? name[1] : "",
      data: body.subarray(split + 4),
    });
    start = next;
  }
  return found;
}

const env = loadEnv();

const server = http.createServer(async (request, response) => {
  const headers = {
    "Access-Control-Allow-Origin": request.headers.origin || "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    Vary: "Origin",
  };
  if (request.method === "OPTIONS") {
    response.writeHead(204, headers);
    response.end();
    return;
  }
  if (request.method !== "POST") {
    response.writeHead(405, { ...headers, "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: "POST only" }));
    return;
  }
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = Buffer.concat(chunks);
  const boundary = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(request.headers["content-type"] || "");
  const fields = parts(body, boundary[1] || boundary[2]);
  const file = fields.find((field) => field.name === "file");
  const target = fields.find((field) => field.name === "target");
  try {
    const docx = await translateDemoPdf(new Uint8Array(file.data), target ? target.data.toString("utf8") : "", env);
    response.writeHead(200, {
      ...headers,
      "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    response.end(Buffer.from(docx));
    console.log("demo ok", docx.length);
  } catch (error) {
    response.writeHead(502, { ...headers, "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: String(error.message || "Could not translate this page.").slice(0, 200) }));
    console.log("demo failed", String(error.message || error).slice(0, 200));
  }
});

server.listen(8780, "127.0.0.1");
