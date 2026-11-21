// One original PDF page in, a translated DOCX out.
// CloudConvert makes the DOCX. Phrases are translated, then the words
// go back into the same text nodes.

const LANG_NAME = {
  en: "English",
  es: "Spanish",
  de: "German",
  fr: "French",
  it: "Italian",
  pt: "Portuguese",
  nl: "Dutch",
  pl: "Polish",
  ru: "Russian",
  uk: "Ukrainian",
  tr: "Turkish",
  zh: "Chinese",
  ja: "Japanese",
  ko: "Korean",
};

const WORD = /[A-Za-z\u0400-\u04FF]/;
const SKIP = /^(https?:\/\/|www\.)|\S+@\S+/i;
const PAGE_NUMBER = /^\d+$/;
function tokenPattern() {
  return /<w:tab\b[^>]*\/>|<w:br\b[^>]*\/>|<w:t(\s[^>]*)?>([^<]*)<\/w:t>/g;
}

function paragraphPattern() {
  return /<w:p\b[^>]*>[\s\S]*?<\/w:p>/g;
}

export function languageName(code) {
  return LANG_NAME[String(code || "").toLowerCase()] || "";
}

function crc32(bytes) {
  let crc = ~0;
  for (let i = 0; i < bytes.length; i++) {
    crc ^= bytes[i];
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return ~crc >>> 0;
}

function concat(parts) {
  const size = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

async function inflateRaw(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function findCentralDirectory(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const start = Math.max(0, bytes.length - 66000);
  for (let offset = bytes.length - 22; offset >= start; offset--) {
    if (view.getUint32(offset, true) !== 0x06054b50) continue;
    return {
      count: view.getUint16(offset + 10, true),
      offset: view.getUint32(offset + 16, true),
    };
  }
  throw new Error("DOCX is not a zip file.");
}

export async function readZip(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const central = findCentralDirectory(bytes);
  const files = [];
  let cursor = central.offset;
  for (let index = 0; index < central.count; index++) {
    if (view.getUint32(cursor, true) !== 0x02014b50) throw new Error("DOCX is not a zip file.");
    const method = view.getUint16(cursor + 10, true);
    const compressedSize = view.getUint32(cursor + 20, true);
    const nameLength = view.getUint16(cursor + 28, true);
    const extraLength = view.getUint16(cursor + 30, true);
    const commentLength = view.getUint16(cursor + 32, true);
    const localOffset = view.getUint32(cursor + 42, true);
    const name = new TextDecoder().decode(bytes.subarray(cursor + 46, cursor + 46 + nameLength));
    const nameLengthLocal = view.getUint16(localOffset + 26, true);
    const extraLengthLocal = view.getUint16(localOffset + 28, true);
    const dataStart = localOffset + 30 + nameLengthLocal + extraLengthLocal;
    const compressed = bytes.subarray(dataStart, dataStart + compressedSize);
    let data = compressed;
    if (method === 8) data = await inflateRaw(compressed);
    else if (method !== 0) throw new Error("DOCX uses an unsupported zip method.");
    files.push({ name, data });
    cursor += 46 + nameLength + extraLength + commentLength;
  }
  return files;
}

export function writeZip(files) {
  const encoder = new TextEncoder();
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const file of files) {
    const name = encoder.encode(file.name);
    const data = file.data;
    const local = new Uint8Array(30 + name.length);
    const localView = new DataView(local.buffer);
    localView.setUint32(0, 0x04034b50, true);
    localView.setUint16(4, 20, true);
    localView.setUint32(14, crc32(data), true);
    localView.setUint32(18, data.length, true);
    localView.setUint32(22, data.length, true);
    localView.setUint16(26, name.length, true);
    local.set(name, 30);
    locals.push(local, data);
    const central = new Uint8Array(46 + name.length);
    const centralView = new DataView(central.buffer);
    centralView.setUint32(0, 0x02014b50, true);
    centralView.setUint16(4, 20, true);
    centralView.setUint16(6, 20, true);
    centralView.setUint32(16, crc32(data), true);
    centralView.setUint32(20, data.length, true);
    centralView.setUint32(24, data.length, true);
    centralView.setUint16(28, name.length, true);
    centralView.setUint32(42, offset, true);
    central.set(name, 46);
    centrals.push(central);
    offset += local.length + data.length;
  }
  const centralOffset = offset;
  const centralSize = centrals.reduce((sum, part) => sum + part.length, 0);
  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  endView.setUint32(0, 0x06054b50, true);
  endView.setUint16(8, files.length, true);
  endView.setUint16(10, files.length, true);
  endView.setUint32(12, centralSize, true);
  endView.setUint32(16, centralOffset, true);
  return concat([...locals, ...centrals, end]);
}

function decodeXml(text) {
  return text
    .replace(/&#(\d+);/g, (_, value) => String.fromCharCode(Number(value)))
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}

function encodeXml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function usable(text) {
  const core = text.trim();
  return Boolean(core) && WORD.test(core) && !SKIP.test(core) && !PAGE_NUMBER.test(core);
}

function phrasesIn(paragraph) {
  const groups = [];
  let current = [];
  function flush() {
    if (current.length) groups.push(current);
    current = [];
  }
  for (const token of paragraph.tokens) {
    if (token.kind !== "t") {
      flush();
      continue;
    }
    if (!usable(token.text)) continue;
    current.push(token);
    const text = current.map((item) => item.text.trim()).join(" ");
    if (/[.!?]$/.test(text) || text.split(/\s+/).length >= 28) flush();
  }
  flush();
  return groups;
}

function parseParagraph(xml) {
  const tokens = [];
  for (const match of xml.matchAll(tokenPattern())) {
    if (/^<w:t(\s|>)/.test(match[0])) {
      tokens.push({
        kind: "t",
        attrs: match[1] || "",
        text: decodeXml(match[2] || ""),
        index: match.index,
        length: match[0].length,
      });
    } else {
      tokens.push({ kind: "break", index: match.index });
    }
  }
  return { xml, tokens };
}

function distribute(translation, nodes) {
  const words = translation.split(/\s+/).filter(Boolean);
  const count = nodes.length;
  if (!words.length) return Array(count).fill("");
  if (count === 1 || words.length === 1) return [translation.trim(), ...Array(count - 1).fill("")];
  if (words.length <= count) return words.concat(Array(count - words.length).fill(""));
  const weights = nodes.map((node) => Math.max(node.text.trim().length, 1));
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  const shares = [];
  let taken = 0;
  let filled = 0;
  weights.forEach((weight, index) => {
    filled += weight;
    let target = index === count - 1 ? words.length : Math.round((filled / total) * words.length);
    target = Math.min(Math.max(target, taken + 1), words.length - (count - index - 1));
    shares.push(words.slice(taken, target));
    taken = target;
  });
  return shares.map((share) => share.join(" "));
}

function writeGroup(nodes, pieces) {
  nodes.forEach((node, index) => {
    const raw = node.text || "";
    let lead = raw.slice(0, raw.length - raw.trimStart().length);
    let tail = raw.slice(raw.trimEnd().length);
    const piece = pieces[index] || "";
    if (piece && pieces[index + 1]) {
      const next = nodes[index + 1].text || "";
      const nextLead = next.slice(0, next.length - next.trimStart().length);
      if (!tail && !nextLead) tail = " ";
    }
    node.text = `${lead}${piece}${tail}`;
    if ((node.text.startsWith(" ") || node.text.endsWith(" ")) && !/xml:space=/.test(node.attrs)) {
      node.attrs += ' xml:space="preserve"';
    }
  });
}

function rebuildParagraph(paragraph) {
  let xml = paragraph.xml;
  const texts = paragraph.tokens.filter((token) => token.kind === "t").sort((a, b) => b.index - a.index);
  for (const token of texts) {
    const tag = `<w:t${token.attrs}>${encodeXml(token.text)}</w:t>`;
    xml = xml.slice(0, token.index) + tag + xml.slice(token.index + token.length);
  }
  return xml;
}

export function phraseJobs(xml) {
  const paragraphs = [];
  const jobs = [];
  for (const match of xml.matchAll(paragraphPattern())) {
    const paragraph = parseParagraph(match[0]);
    paragraphs.push({ start: match.index, length: match[0].length, paragraph });
    jobs.push(...phrasesIn(paragraph));
  }
  return { paragraphs, jobs };
}

export function applyPhrases(xml, translations) {
  const { paragraphs, jobs } = phraseJobs(xml);
  jobs.forEach((group, index) => writeGroup(group, distribute(translations[index] || "", group)));
  let next = xml;
  for (const item of paragraphs.sort((a, b) => b.start - a.start)) {
    const rebuilt = rebuildParagraph(item.paragraph);
    next = next.slice(0, item.start) + rebuilt + next.slice(item.start + item.length);
  }
  return next;
}

function skeleton(xml) {
  return xml.replace(/<w:t\b[^>]*>[\s\S]*?<\/w:t>/g, "");
}

export async function translateDocxBytes(docxBytes, translateLines) {
  const files = await readZip(docxBytes);
  const originals = new Map();
  const jobs = [];
  const targets = [];
  for (const file of files) {
    if (!file.name.startsWith("word/") || !file.name.endsWith(".xml")) continue;
    const xml = new TextDecoder().decode(file.data);
    originals.set(file.name, skeleton(xml));
    const parsed = phraseJobs(xml);
    if (!parsed.jobs.length) continue;
    targets.push({ file, parsed });
    jobs.push(...parsed.jobs.map((group) => group.map((node) => node.text.trim()).join(" ")));
  }
  const translated = jobs.length ? await translateLines(jobs) : [];
  let cursor = 0;
  for (const target of targets) {
    const slice = translated.slice(cursor, cursor + target.parsed.jobs.length);
    cursor += target.parsed.jobs.length;
    const xml = new TextDecoder().decode(target.file.data);
    const next = applyPhrases(xml, slice);
    if (skeleton(next) !== originals.get(target.file.name)) {
      throw new Error("The DOCX structure changed.");
    }
    target.file.data = new TextEncoder().encode(next);
  }
  return writeZip(files);
}

async function outputText(response) {
  const payload = await response.json();
  if (!response.ok) {
    const message = payload.error && payload.error.message ? payload.error.message : "OpenAI did not translate this page.";
    throw new Error(message);
  }
  if (typeof payload.output_text === "string" && payload.output_text.trim()) return payload.output_text;
  const parts = [];
  for (const item of payload.output || []) {
    for (const content of item.content || []) {
      if (content.text) parts.push(content.text);
    }
  }
  return parts.join("\n");
}

async function oneChunk(env, lines) {
  const source = lines.map((line, index) => `[${index}] ${line}`).join("\n");
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.OPENAI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: env.OPENAI_MODEL || "gpt-5-mini",
      reasoning: { effort: "minimal" },
      max_output_tokens: 8000,
      instructions: `Translate each line into ${env.TARGET_NAME}. Return exactly ${lines.length} lines. Keep each [n] tag. Keep personal names and product names as written. Translate the whole line as one phrase. Do not add notes.`,
      input: source,
    }),
  });
  const text = await outputText(response);
  const found = [...text.matchAll(/\[(\d+)\]\s*(.*)/g)];
  if (found.length !== lines.length) return null;
  const ordered = Array(lines.length).fill("");
  for (const match of found) {
    const index = Number(match[1]);
    if (index < 0 || index >= lines.length || ordered[index]) return null;
    ordered[index] = match[2].trim();
  }
  if (ordered.some((line) => !line)) return null;
  return ordered;
}

async function translateLines(env, lines) {
  const done = [];
  while (done.length < lines.length) {
    const chunk = lines.slice(done.length, done.length + 6);
    let translated = await oneChunk(env, chunk);
    if (!translated) {
      translated = [];
      for (const line of chunk) {
        const solo = await oneChunk(env, [line]);
        if (!solo) throw new Error("OpenAI did not translate this page.");
        translated.push(solo[0]);
      }
    }
    done.push(...translated);
  }
  return done;
}

async function cloudconvertDocx(env, pdfBytes) {
  const created = await fetch("https://api.cloudconvert.com/v2/jobs", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.CLOUDCONVERT_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      tasks: {
        "import-file": { operation: "import/upload" },
        "convert-file": {
          operation: "convert",
          input: "import-file",
          input_format: "pdf",
          output_format: "docx",
        },
        "export-file": { operation: "export/url", input: "convert-file" },
      },
    }),
  });
  const createdBody = await created.json();
  if (!created.ok) throw new Error("CloudConvert did not accept this page.");
  const tasks = Object.fromEntries(createdBody.data.tasks.map((task) => [task.name, task]));
  const form = tasks["import-file"].result.form;
  const upload = new FormData();
  for (const [name, value] of Object.entries(form.parameters)) upload.append(name, value);
  upload.append("file", new Blob([pdfBytes], { type: "application/pdf" }), "page.pdf");
  const uploaded = await fetch(form.url, { method: "POST", body: upload });
  if (!uploaded.ok) throw new Error("CloudConvert did not accept this page.");
  const jobId = createdBody.data.id;
  for (let attempt = 0; attempt < 40; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const jobResponse = await fetch(`https://api.cloudconvert.com/v2/jobs/${jobId}`, {
      headers: { Authorization: `Bearer ${env.CLOUDCONVERT_API_KEY}` },
    });
    const job = await jobResponse.json();
    if (!jobResponse.ok) throw new Error("CloudConvert did not convert this page.");
    if (job.data.status === "finished") {
      const exported = job.data.tasks.find((task) => task.name === "export-file");
      const fileResponse = await fetch(exported.result.files[0].url);
      if (!fileResponse.ok) throw new Error("CloudConvert did not return the page.");
      return new Uint8Array(await fileResponse.arrayBuffer());
    }
    if (job.data.status === "error") throw new Error("CloudConvert could not convert this page.");
  }
  throw new Error("CloudConvert is still converting this page.");
}

export async function translateDemoPdf(pdfBytes, targetCode, env) {
  const targetName = languageName(targetCode);
  if (!targetName) throw new Error("Choose a target language.");
  if (!env.CLOUDCONVERT_API_KEY || !env.OPENAI_API_KEY) throw new Error("Demo is not connected yet.");
  const docx = await cloudconvertDocx(env, pdfBytes);
  return translateDocxBytes(docx, (lines) => translateLines({ ...env, TARGET_NAME: targetName }, lines));
}
