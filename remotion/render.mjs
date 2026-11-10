#!/usr/bin/env node
/**
 * Bundle-once-render-many orchestrator for claude-shorts.
 *
 * Bundles the Remotion project once, opens a shared Chrome instance,
 * then renders each approved segment sequentially.
 *
 * Usage:
 *   node render.mjs \
 *     --segments WORK/snapped_segments.json \
 *     --reframe  WORK/reframe.json \
 *     --captions WORK/captions.json \
 *     --clips-dir WORK/clips/ \
 *     --output-dir WORK/render/ \
 *     [--style bold|bounce|clean] [--fps auto|30|60] [--background blur|black]
 *     [--only 2,5] [--concurrency N] [--no-progress-bar] [--no-hook]
 *
 * Progress is printed as one JSON object per line. Exits non-zero if any
 * segment fails to render.
 *
 * Set SHORTS_CHROME=/path/to/chrome to use an existing Chrome/Chromium instead
 * of the Chrome Headless Shell that Remotion downloads on first use.
 */
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition, openBrowser } from "@remotion/renderer";
import path from "path";
import fs from "fs";
import http from "http";
import { fileURLToPath } from "url";
import { parseArgs } from "util";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STYLES = ["bold", "bounce", "clean"];

const log = (obj) => console.log(JSON.stringify(obj));
// Errors go to stdout as JSON too, like every other script in the pipeline.
const die = (message, extra = {}) => {
  console.log(JSON.stringify({ error: message, ...extra }));
  process.exit(1);
};

function readJson(file, label) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch (err) {
    die(`Could not read ${label}: ${file}`, { detail: err.message });
  }
}

const MIME = {
  ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
  ".mkv": "video/x-matroska", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
  ".webp": "image/webp",
};

/**
 * Serve clip files over HTTP (with Range support) for the Remotion renderer.
 * `extraFiles` maps a URL name to an absolute path (b-roll that lives elsewhere);
 * nothing outside the clips directory and that map is reachable.
 */
function startClipServer(clipsDir, extraFiles = new Map()) {
  const root = path.resolve(clipsDir);
  const server = http.createServer((req, res) => {
    const name = path.basename(decodeURIComponent((req.url || "/").split("?")[0]));
    const filePath = extraFiles.get(name) || path.join(root, name);
    if (!name || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    const { size } = fs.statSync(filePath);
    const type = MIME[path.extname(filePath).toLowerCase()] || "application/octet-stream";
    const headers = { "Content-Type": type, "Accept-Ranges": "bytes" };
    const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range || "");
    if (range && (range[1] || range[2])) {
      const start = range[1] ? parseInt(range[1], 10) : Math.max(0, size - parseInt(range[2], 10));
      const end = range[1] && range[2] ? Math.min(parseInt(range[2], 10), size - 1) : size - 1;
      if (start > end || start >= size) {
        res.writeHead(416, { "Content-Range": `bytes */${size}` });
        res.end();
        return;
      }
      res.writeHead(206, {
        ...headers,
        "Content-Range": `bytes ${start}-${end}/${size}`,
        "Content-Length": end - start + 1,
      });
      fs.createReadStream(filePath, { start, end }).pipe(res);
      return;
    }
    res.writeHead(200, { ...headers, "Content-Length": size });
    fs.createReadStream(filePath).pipe(res);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () =>
      resolve({ server, baseUrl: `http://127.0.0.1:${server.address().port}` })
    );
  });
}

/** Output frame rate: follow the source, within what the platforms accept. */
function pickFps(option, sourceFps) {
  if (option !== "auto") {
    const fixed = Number(option);
    if (!Number.isFinite(fixed) || fixed < 1 || fixed > 120) die(`Invalid --fps: ${option}`);
    return Math.round(fixed);
  }
  let fps = Number(sourceFps) || 30;
  while (fps > 61) fps /= 2; // 120 -> 60
  if (fps < 20) fps = 30;
  return Math.round(fps); // 29.97 -> 30, 23.976 -> 24, 59.94 -> 60
}

/**
 * Where to punch in: the joins left by jump cuts, on the short's timeline.
 * Joins that follow the previous one too closely are skipped so the framing
 * does not flicker.
 */
function punchInTimes(seg, minGap = 1.5) {
  const keep = seg.keep || [];
  const times = [];
  let elapsed = 0;
  let last = -Infinity;
  for (let i = 0; i < keep.length - 1; i++) {
    elapsed += keep[i][1] - keep[i][0];
    if (elapsed - last >= minGap) {
      times.push(Number(elapsed.toFixed(3)));
      last = elapsed;
    }
  }
  return times;
}

/** Captions for a segment, in clip-local milliseconds. */
function captionsFor(captionData, seg, segId) {
  if (captionData.by_segment) {
    return captionData.by_segment[String(segId)] || [];
  }
  // Flat word list in source time (raw transcript): slice to the segment.
  const all = captionData.captions || [];
  const startMs = seg.start * 1000;
  const endMs = seg.end * 1000;
  return all
    .filter((c) => c.startMs >= startMs - 50 && c.endMs <= endMs + 50)
    .map((c) => ({
      ...c,
      startMs: Math.max(0, c.startMs - startMs),
      endMs: Math.max(0, c.endMs - startMs),
    }));
}

const USAGE = `Usage: node render.mjs --segments FILE --reframe FILE --captions FILE --clips-dir DIR --output-dir DIR
  [--style bold|bounce|clean]   caption style (default: from segments file, else bold)
  [--fps auto|N]                output frame rate (default: follow source, max 60)
  [--background blur|black]     backdrop for the framed (screen recording) layout
  [--only 2,5]                  render only these segment ids
  [--broll FILE]                b-roll cutaways (broll.json from broll.py apply)
  [--no-punch-in]               do not alternate a slight zoom at jump cuts
  [--punch-zoom 1.08]           how far to zoom in on alternate jump-cut sections
  [--concurrency N]             parallel browser tabs (default: half the CPU cores)
  [--crf N]                     x264 quality of the intermediate (default 16)
  [--no-hook] [--no-progress-bar]`;

async function main() {
  if (process.argv.includes("--help") || process.argv.includes("-h")) {
    console.log(USAGE);
    return;
  }
  const { values: args } = parseArgs({
    options: {
      segments: { type: "string" },
      reframe: { type: "string" },
      captions: { type: "string" },
      "clips-dir": { type: "string" },
      "output-dir": { type: "string" },
      style: { type: "string" },
      fps: { type: "string", default: "auto" },
      background: { type: "string", default: "blur" },
      only: { type: "string" },
      concurrency: { type: "string" },
      broll: { type: "string" },
      "punch-zoom": { type: "string", default: "1.08" },
      "no-punch-in": { type: "boolean", default: false },
      crf: { type: "string", default: "16" },
      "no-progress-bar": { type: "boolean", default: false },
      "no-hook": { type: "boolean", default: false },
    },
    strict: true,
  });

  const clipsDir = args["clips-dir"];
  const outputDir = args["output-dir"];
  if (!args.segments || !args.reframe || !args.captions || !clipsDir || !outputDir) {
    die("Missing required arguments", { usage: USAGE });
  }

  const segmentsData = readJson(args.segments, "segments");
  const reframeData = readJson(args.reframe, "reframe data");
  const captionData = readJson(args.captions, "captions");

  const style = args.style || segmentsData.style || "bold";
  if (!STYLES.includes(style)) die(`Unknown caption style: ${style}`, { valid: STYLES });
  if (!["blur", "black"].includes(args.background)) die(`Unknown background: ${args.background}`);

  // Reject bad numbers now, before the slow bundle and before a browser is open.
  if (args.fps !== "auto") pickFps(args.fps, 30);
  const crf = Number(args.crf);
  if (!Number.isFinite(crf) || crf < 0 || crf > 51) die(`Invalid --crf: ${args.crf}`);
  const punchZoom = Number(args["punch-zoom"]);
  if (!Number.isFinite(punchZoom) || punchZoom < 1 || punchZoom > 2) {
    die(`Invalid --punch-zoom: ${args["punch-zoom"]} (expected 1.0-2.0)`);
  }
  let concurrency = null;
  if (args.concurrency !== undefined) {
    concurrency = Number(args.concurrency);
    if (!Number.isInteger(concurrency) || concurrency < 1) die(`Invalid --concurrency: ${args.concurrency}`);
  }

  // Give every segment its id (position in the full list if it has none)
  // before filtering, so --only and the clip/output names always agree.
  const only = args.only ? new Set(args.only.split(",").map((s) => s.trim())) : null;
  const segments = (segmentsData.segments || [])
    .map((seg, i) => ({ ...seg, id: seg.id ?? i + 1 }))
    .filter((seg) => !only || only.has(String(seg.id)));
  if (segments.length === 0) die("No segments to render");

  fs.mkdirSync(outputDir, { recursive: true });

  // B-roll files can live anywhere; expose each one under a generated name.
  const brollData = args.broll ? readJson(args.broll, "b-roll plan") : { by_segment: {} };
  const extraFiles = new Map();
  const brollBySegment = {};
  for (const [segId, items] of Object.entries(brollData.by_segment || {})) {
    brollBySegment[segId] = items.map((item) => {
      const name = `broll_${extraFiles.size}${path.extname(item.path).toLowerCase()}`;
      extraFiles.set(name, path.resolve(item.path));
      return { name, kind: item.kind, start: item.start, duration: item.duration, sourceStart: item.source_start || 0 };
    });
  }

  const { server: clipServer, baseUrl } = await startClipServer(clipsDir, extraFiles);

  log({ action: "bundle", status: "starting" });
  const startBundle = Date.now();
  const serveUrl = await bundle({ entryPoint: path.resolve(__dirname, "src/index.ts") });
  log({
    action: "bundle",
    status: "complete",
    time_sec: Number(((Date.now() - startBundle) / 1000).toFixed(1)),
  });

  const browserExecutable = process.env.SHORTS_CHROME || undefined;
  const browser = await openBrowser("chrome", { browserExecutable });

  const results = [];
  try {
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      const segId = seg.id ?? i + 1;
      const tag = String(segId).padStart(2, "0");
      const clipName = `clip_${tag}.mp4`;
      const outputPath = path.resolve(outputDir, `short_${tag}.mp4`);
      fs.rmSync(outputPath, { force: true }); // never leave a stale render behind

      if (!fs.existsSync(path.resolve(clipsDir, clipName))) {
        log({ action: "render", segment: segId, status: "failed", error: `Clip not found: ${clipName}` });
        results.push({ segment: segId, output: outputPath, status: "failed" });
        continue;
      }

      const reframe = reframeData.clips?.[clipName];
      if (!reframe?.crop) {
        log({ action: "render", segment: segId, status: "failed", error: `No reframe data for ${clipName} — run compute_reframe.py` });
        results.push({ segment: segId, output: outputPath, status: "failed" });
        continue;
      }
      const [srcW, srcH] = String(reframe.source_resolution).split("x").map(Number);
      // Jump-cut clips are shorter than the source range they came from.
      const durationInSeconds = seg.render_duration ?? seg.end - seg.start;
      const fps = pickFps(args.fps, reframe.fps);
      const captions = captionsFor(captionData, seg, segId);

      const inputProps = {
        clipSrc: `${baseUrl}/${clipName}`,
        sourceWidth: srcW,
        sourceHeight: srcH,
        layout: reframe.layout || "fill",
        crop: reframe.crop,
        cropKeyframes: reframe.crop_keyframes || [],
        cropMode: reframe.crop_mode || "pan",
        // Punch-ins suit people on camera; on screen recordings they just look like a glitch.
        punchIns: args["no-punch-in"] || (reframe.layout || "fill") === "framed" ? [] : punchInTimes(seg),
        punchZoom,
        broll: (brollBySegment[String(segId)] || []).map(({ name, ...rest }) => ({
          src: `${baseUrl}/${name}`,
          ...rest,
        })),
        panels: reframe.panels || [],
        background: args.background,
        captions,
        captionStyle: style,
        hookLine1: args["no-hook"] ? "" : seg.hook_line1 || "",
        hookLine2: args["no-hook"] ? "" : seg.hook_line2 || "",
        showProgressBar: !args["no-progress-bar"],
        durationInSeconds,
        fps,
      };

      log({
        action: "render",
        segment: segId,
        index: i + 1,
        total: segments.length,
        duration_sec: Number(durationInSeconds.toFixed(1)),
        fps,
        layout: inputProps.layout,
        caption_words: captions.length,
        punch_ins: inputProps.punchIns.length,
        broll: inputProps.broll.length,
        status: "starting",
      });

      let rendered = false;
      const maxAttempts = 2;
      for (let attempt = 1; attempt <= maxAttempts && !rendered; attempt++) {
        try {
          const startRender = Date.now();
          let lastReported = 0;

          // selectComposition resolves calculateMetadata (duration + fps per segment)
          const composition = await selectComposition({
            serveUrl,
            id: "ShortVideo",
            inputProps,
            puppeteerInstance: browser,
          });

          await renderMedia({
            composition,
            serveUrl,
            codec: "h264",
            crf,
            // High-quality mezzanine: export.sh re-encodes this per platform.
            jpegQuality: 92,
            colorSpace: "bt709",
            audioBitrate: "320k",
            outputLocation: outputPath,
            inputProps,
            puppeteerInstance: browser,
            concurrency,
            onProgress: ({ progress }) => {
              const pct = Math.floor(progress * 100);
              if (pct >= lastReported + 25 && pct < 100) {
                lastReported = pct;
                log({ action: "render", segment: segId, status: "progress", percent: pct });
              }
            },
          });

          const result = {
            action: "render",
            segment: segId,
            output: outputPath,
            duration_sec: Number(durationInSeconds.toFixed(1)),
            render_time_sec: Number(((Date.now() - startRender) / 1000).toFixed(1)),
            file_size_mb: Number((fs.statSync(outputPath).size / 1024 / 1024).toFixed(1)),
            status: "complete",
          };
          results.push(result);
          log(result);
          rendered = true;
        } catch (renderErr) {
          fs.rmSync(outputPath, { force: true });
          log({
            action: "render",
            segment: segId,
            status: attempt < maxAttempts ? "retrying" : "failed",
            attempt,
            error: renderErr.message,
          });
        }
      }

      if (!rendered) {
        results.push({ segment: segId, output: outputPath, status: "failed" });
      }
    }
  } finally {
    await browser.close({ silent: true });
    clipServer.close();
  }

  const failed = results.filter((r) => r.status !== "complete");
  log({
    action: "render_complete",
    segments_rendered: results.length - failed.length,
    segments_failed: failed.length,
    total_segments: segments.length,
    output_dir: outputDir,
    results,
  });
  if (failed.length > 0) process.exit(2);
}

main().catch((err) => {
  console.log(JSON.stringify({ error: err.message, stack: err.stack }));
  process.exit(1);
});
