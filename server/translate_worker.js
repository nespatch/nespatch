// translate_worker.js — Cloudflare Worker.
// A demo request translates one PDF page and returns a DOCX.
// Any other request still sends the whole PDF to DeepL.
//
// Deploy this file together with demo_page.js.
// Secrets:
//   DEEPL_AUTH_KEY
//   DEEPL_API_URL              (https://api-free.deepl.com or https://api.deepl.com)
//   SUPABASE_URL
//   SUPABASE_SERVICE_ROLE_KEY
//   CLOUDCONVERT_API_KEY
//   OPENAI_API_KEY
//   PADDLE_API_KEY             (server key, pdl_live_..., not the client token)
//
// The site sends the signed-in user's access token.
// One credit is spent only after DeepL returns the file.

import { languageName, translateDemoPdf } from "./demo_page.js";

const DEEPL_LANG = {
  en: "EN",
  es: "ES",
  de: "DE",
  fr: "FR",
  it: "IT",
  pt: "PT",
  nl: "NL",
  pl: "PL",
  ru: "RU",
  uk: "UK",
  tr: "TR",
  zh: "ZH",
  ja: "JA",
  ko: "KO",
};

const MAX_BYTES = 10 * 1024 * 1024;
const POLL_TIMES = 40;
const POLL_WAIT_MS = 3000;
const PADDLE_PRODUCT_ID = "pro_01m7z7m1526dq8bet73n2t3mrf";
const PADDLE_API = "https://api.paddle.com";
const PDF_BUCKET = "pdf-orders";
const PDF_MAX_BYTES = 80 * 1024 * 1024;

function cors(request) {
  const origin = request.headers.get("Origin") || "*";
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    Vary: "Origin",
  };
}

function json(request, status, message) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { ...cors(request), "Content-Type": "application/json" },
  });
}

function jsonData(request, status, payload) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { ...cors(request), "Content-Type": "application/json" },
  });
}

function filePrice(pages, chars) {
  const characters = chars == null ? pages * 1700 : chars;
  const cost = (characters / 1000000) * 1.46 + 0.05 + Math.max(0, pages - 1) * 0.0002;
  const breakeven = (cost + 0.5) / 0.95;
  return Math.ceil(breakeven * 2);
}

async function paddleFetch(env, path, options) {
  const headers = new Headers(options && options.headers);
  headers.set("Authorization", `Bearer ${env.PADDLE_API_KEY}`);
  if (options && options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${PADDLE_API}${path}`, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, payload };
}

async function paddleCustomer(env, email) {
  const listed = await paddleFetch(env, `/customers?email=${encodeURIComponent(email)}`);
  const found = listed.ok && listed.payload.data && listed.payload.data[0];
  if (found) return found.id;
  const created = await paddleFetch(env, "/customers", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
  if (created.ok && created.payload.data) return created.payload.data.id;
  const existing = created.payload.error && created.payload.error.meta && created.payload.error.meta.customer_id;
  if (existing) return existing;
  throw new Error("Could not start payment.");
}

async function ensurePdfBucket(env) {
  const response = await fetch(`${env.SUPABASE_URL}/storage/v1/bucket`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ id: PDF_BUCKET, name: PDF_BUCKET, public: false }),
  });
  if (response.ok || response.status === 409) return;
  const detail = await response.text();
  if (/exist/i.test(detail)) return;
  throw new Error("Could not store the file.");
}

async function storePdf(env, bytes) {
  await ensurePdfBucket(env);
  const path = `orders/${crypto.randomUUID()}.pdf`;
  const response = await fetch(`${env.SUPABASE_URL}/storage/v1/object/${PDF_BUCKET}/${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      "Content-Type": "application/pdf",
    },
    body: bytes,
  });
  if (!response.ok) throw new Error("Could not store the file.");
  return path;
}

async function handleFileCheckout(request, env) {
  if (!env.PADDLE_API_KEY || !env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return json(request, 502, "Payment is not connected yet.");
  }
  let form;
  try {
    form = await request.formData();
  } catch {
    return json(request, 400, "Send the file.");
  }
  const file = form.get("file");
  const email = String(form.get("email") || "").trim().toLowerCase();
  const pages = Number(form.get("pages"));
  const charsRaw = form.get("chars");
  const chars = charsRaw == null || charsRaw === "" ? null : Number(charsRaw);
  if (!file || typeof file.arrayBuffer !== "function") return json(request, 400, "Send the file.");
  if (file.size > PDF_MAX_BYTES) return json(request, 413, "That PDF is larger than 80 MB.");
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254) {
    return json(request, 400, "Enter a valid email.");
  }
  if (!Number.isInteger(pages) || pages < 1 || pages > 5000) {
    return json(request, 400, "Choose a file first.");
  }
  if (chars != null && (!Number.isFinite(chars) || chars < 0)) {
    return json(request, 400, "Choose a file first.");
  }
  const price = filePrice(pages, chars);
  const characters = chars == null ? pages * 1700 : chars;
  try {
    const path = await storePdf(env, new Uint8Array(await file.arrayBuffer()));
    const customerId = await paddleCustomer(env, email);
    const created = await paddleFetch(env, "/transactions", {
      method: "POST",
      body: JSON.stringify({
        items: [{
          quantity: 1,
          price: {
            description: "Full file translation",
            name: "Full file",
            billing_cycle: null,
            tax_mode: "account_setting",
            unit_price: {
              amount: String(price * 100),
              currency_code: "USD",
            },
            product_id: PADDLE_PRODUCT_ID,
          },
        }],
        customer_id: customerId,
        collection_mode: "automatic",
        custom_data: {
          email,
          pages: String(pages),
          characters: String(characters),
          price: String(price),
          bucket: PDF_BUCKET,
          path,
        },
      }),
    });
    const transactionId = created.payload.data && created.payload.data.id;
    if (!created.ok || !transactionId) throw new Error("Could not start payment.");
    return jsonData(request, 200, { transactionId, price, path });
  } catch (error) {
    return json(request, 502, String(error.message || "Could not start payment.").slice(0, 200));
  }
}

async function userId(env, token) {
  const response = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
    headers: {
      Authorization: `Bearer ${token}`,
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
    },
  });
  if (!response.ok) return null;
  const user = await response.json();
  return user && user.id ? user.id : null;
}

async function creditBalance(env, userId) {
  const response = await fetch(
    `${env.SUPABASE_URL}/rest/v1/credits?user_id=eq.${userId}&select=balance`,
    {
      headers: {
        apikey: env.SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      },
    }
  );
  if (!response.ok) return 0;
  const rows = await response.json();
  return rows[0] && typeof rows[0].balance === "number" ? rows[0].balance : 0;
}

async function spendCredit(env, userId) {
  const response = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/spend_credit`, {
    method: "POST",
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ p_user: userId }),
  });
  if (!response.ok) throw new Error(await response.text());
}

function deeplLang(value) {
  return DEEPL_LANG[String(value || "").toLowerCase()] || "";
}

async function deepl(env, path, options) {
  const base = (env.DEEPL_API_URL || "https://api-free.deepl.com").replace(/\/$/, "");
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `DeepL-Auth-Key ${env.DEEPL_AUTH_KEY}`);
  const response = await fetch(`${base}${path}`, { ...options, headers });
  return response;
}

async function handleDemo(request, env, form) {
  const file = form.get("file");
  const target = String(form.get("target") || "");
  if (!file || typeof file.arrayBuffer !== "function") return json(request, 400, "Send a PDF.");
  if (!languageName(target)) return json(request, 400, "Choose a target language.");
  if (file.size > 8 * 1024 * 1024) return json(request, 413, "That page is too large.");
  try {
    const docx = await translateDemoPdf(new Uint8Array(await file.arrayBuffer()), target, env);
    return new Response(docx, {
      status: 200,
      headers: {
        ...cors(request),
        "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": 'inline; filename="page.docx"',
      },
    });
  } catch (error) {
    return json(request, 502, String(error.message || "Could not translate this page.").slice(0, 200));
  }
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(request) });
    }
    if (request.method !== "POST") {
      return json(request, 405, "POST only");
    }
    const url = new URL(request.url);
    if (url.pathname === "/checkout") return handleFileCheckout(request, env);

    let form;
    try {
      form = await request.formData();
    } catch {
      return json(request, 400, "Send a PDF.");
    }
    if (form.get("demo") === "1") return handleDemo(request, env, form);

    const token = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
    const id = await userId(env, token);
    if (!id) return json(request, 401, "Sign in first.");

    const balance = await creditBalance(env, id);
    if (balance < 1) return json(request, 402, "Buy a credit first.");

    const file = form.get("file");
    const target = deeplLang(form.get("target"));
    const source = deeplLang(form.get("source"));
    if (!file || typeof file.arrayBuffer !== "function") {
      return json(request, 400, "Send a PDF.");
    }
    if (!target) return json(request, 400, "Choose a target language.");
    if (file.size > MAX_BYTES) return json(request, 413, "PDF is larger than 10 MB.");

    const upload = new FormData();
    upload.append("target_lang", target);
    if (source) upload.append("source_lang", source);
    upload.append("file", file, file.name || "document.pdf");

    const started = await deepl(env, "/v2/document", { method: "POST", body: upload });
    if (!started.ok) return json(request, 502, "DeepL did not accept the PDF.");
    const startedBody = await started.json();
    const documentId = startedBody.document_id;
    const documentKey = startedBody.document_key;
    if (!documentId || !documentKey) return json(request, 502, "DeepL did not accept the PDF.");

    let done = false;
    for (let i = 0; i < POLL_TIMES; i++) {
      await new Promise((resolve) => setTimeout(resolve, POLL_WAIT_MS));
      const statusResponse = await deepl(env, `/v2/document/${documentId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_key: documentKey }),
      });
      if (!statusResponse.ok) return json(request, 502, "DeepL status check failed.");
      const statusBody = await statusResponse.json();
      if (statusBody.status === "done") {
        done = true;
        break;
      }
      if (statusBody.status === "error") {
        return json(request, 502, statusBody.error_message || statusBody.message || "DeepL could not translate this PDF.");
      }
    }
    if (!done) return json(request, 504, "DeepL is still translating this PDF. Wait a moment and try again.");

    const result = await deepl(env, `/v2/document/${documentId}/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_key: documentKey }),
    });
    if (!result.ok) return json(request, 502, "DeepL did not return the PDF.");

    try {
      await spendCredit(env, id);
    } catch {
      return json(request, 402, "Buy a credit first.");
    }

    const filename = (file.name || "document.pdf").replace(/\.pdf$/i, "") + " translated.pdf";
    return new Response(result.body, {
      status: 200,
      headers: {
        ...cors(request),
        "Content-Type": "application/pdf",
        "Content-Disposition": `attachment; filename="${filename.replace(/"/g, "")}"`,
      },
    });
  },
};
