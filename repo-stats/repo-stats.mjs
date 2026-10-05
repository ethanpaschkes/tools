#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────────
// repo-stats: a line-count snapshot of the git repo you run it in.
//
//   cd my-repo && node repo-stats.mjs
//
// Counts the files git knows about (`git ls-files --cached --others --exclude-standard`), so anything your
// .gitignore excludes is excluded here too. Prints total lines of code, the largest files, a per-language
// breakdown, test-file counts, and Markdown separately (docs would otherwise inflate "lines of code").
// Writes `.repo-stats.json` in the repo so the next run can show what changed; add it to .gitignore.
// Extra folders to skip: REPO_STATS_SKIP="vendor,old-copies" node repo-stats.mjs
// ─────────────────────────────────────────────────────────────────────────────
import { readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join, extname } from 'node:path';

const ROOT = process.cwd();
const SNAPSHOT = join(ROOT, '.repo-stats.json');

/** Directories that are never this project's own source. */
const SKIP_DIRS = new Set([
  'node_modules', '.next', '.git', 'public', 'design', 'dist', 'build', 'coverage',
  '.netlify', '.vercel',
  '.turbo', '.cache', 'out', 'DerivedData',
]);

/** Full relative paths to skip (build output that lives inside a kept dir). */
const SKIP_PATHS = new Set([join('ios', 'App', 'build')]);
// Extra folders to skip: REPO_STATS_SKIP="vendor,old-copies" node repo-stats.mjs
for (const d of (process.env.REPO_STATS_SKIP || '').split(',').filter(Boolean)) SKIP_DIRS.add(d.trim());

const CODE_EXT = new Map([
  ['.ts', 'TypeScript'], ['.tsx', 'TypeScript (React)'], ['.js', 'JavaScript'],
  ['.mjs', 'JavaScript (ESM)'], ['.cjs', 'JavaScript (CJS)'], ['.jsx', 'JavaScript (React)'],
  ['.swift', 'Swift'], ['.py', 'Python'], ['.css', 'CSS'], ['.sh', 'Shell'],
  /* A language missing from this map is silently counted as nothing: add it here. */
  ['.java', 'Java'], ['.kt', 'Kotlin'],
  ['.mts', 'TypeScript (ESM)'], ['.json', 'JSON'], ['.html', 'HTML'],
]);

/**
 * 🔑 GIT DECIDES WHAT BELONGS TO THIS PROJECT, not a hand-kept list.
 *
 * `--cached --others --exclude-standard` = tracked files plus untracked ones,
 * MINUS everything `.gitignore` excludes. That single flag set removes
 * `node_modules`, `.next`, `ios/App/build` and `co-atc/` (a whole separate
 * third-party project vendored at `.gitignore:67`) without naming any of them,
 * and it keeps working when the next one arrives.
 *
 * ⚠️ `SKIP_DIRS` still applies on top, for things that ARE committed but are
 * not ours to count — `public/` carries the vendored Cesium SDK.
 * ⚠️ Falls back to a plain walk outside a git checkout.
 */
function gitFiles() {
  try {
    const out = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard'], {
      cwd: ROOT, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
    });
    const list = out.split('\n').filter(Boolean);
    return list.length ? list : null;
  } catch {
    return null;
  }
}

function walk(dir, rel = '', out = []) {
  let entries;
  try { entries = readdirSync(dir, { withFileTypes: true }); } catch { return out; }
  for (const e of entries) {
    const relPath = rel ? join(rel, e.name) : e.name;
    if (SKIP_PATHS.has(relPath)) continue;
    if (e.isDirectory()) {
      if (SKIP_DIRS.has(e.name)) continue;
      walk(join(dir, e.name), relPath, out);
    } else if (e.isFile()) {
      out.push(relPath);
    }
  }
  return out;
}

function lineCount(p) {
  try {
    const buf = readFileSync(join(ROOT, p));
    if (buf.includes(0)) return null; // binary
    let n = 0;
    for (const b of buf) if (b === 10) n++;
    return buf.length && buf[buf.length - 1] !== 10 ? n + 1 : n;
  } catch { return null; }
}

const tracked = gitFiles();
const files = (tracked ?? walk(ROOT)).filter((f) => {
  const parts = f.split('/');
  if (parts.some((seg) => SKIP_DIRS.has(seg))) return false;
  return ![...SKIP_PATHS].some((sp) => f.startsWith(sp.replace(/\\/g, '/')));
});
if (!tracked) console.log('  ⚠️  not a git checkout — falling back to a directory walk');
const byLang = new Map();
const codeFiles = [];
let codeLines = 0, mdLines = 0, mdFiles = 0;

for (const f of files) {
  const ext = extname(f).toLowerCase();
  if (ext === '.md') {
    const n = lineCount(f);
    if (n !== null) { mdLines += n; mdFiles++; }
    continue;
  }
  const lang = CODE_EXT.get(ext);
  if (!lang) continue;
  // JSON is data, not code — counted, but kept out of the headline.
  const n = lineCount(f);
  if (n === null) continue;
  const isData = ext === '.json';
  codeFiles.push({ path: f, lines: n, lang, isData });
  if (!isData) {
    codeLines += n;
    const cur = byLang.get(lang) ?? { files: 0, lines: 0 };
    byLang.set(lang, { files: cur.files + 1, lines: cur.lines + n });
  }
}

const source = codeFiles.filter((f) => !f.isData);
const testFiles = source.filter((f) => /\.test\.[tj]sx?$/.test(f.path));
const testLines = testFiles.reduce((a, f) => a + f.lines, 0);

// Test CASES: count `it(` / `test(` call sites. Approximate by design — an
// exact count needs the runner, and `pnpm test` already prints that.
let testCases = 0;
for (const f of testFiles) {
  try { testCases += (readFileSync(join(ROOT, f.path), 'utf8').match(/^\s*(it|test)\s*\(/gm) ?? []).length; } catch { /* skip */ }
}

const prev = existsSync(SNAPSHOT) ? JSON.parse(readFileSync(SNAPSHOT, 'utf8')) : null;
const delta = (now, then) => {
  if (then === undefined || then === null) return '';
  const d = now - then;
  return d === 0 ? '  (no change)' : `  (${d > 0 ? '+' : ''}${d.toLocaleString()})`;
};

const pct = (n) => `${((n / codeLines) * 100).toFixed(1)}%`;
const pad = (s, n) => String(s).padEnd(n);
const num = (n) => n.toLocaleString().padStart(9);

console.log('\n╔═ REPO SNAPSHOT ' + '═'.repeat(52));
if (prev?.at) console.log(`║  previous snapshot: ${prev.at}`);
console.log('╠═ TOTALS ' + '─'.repeat(59));
console.log(`║  Lines of code      ${num(codeLines)}${delta(codeLines, prev?.codeLines)}`);
console.log(`║  Source files       ${num(source.length)}${delta(source.length, prev?.sourceFiles)}`);
console.log(`║  Markdown (docs)    ${num(mdLines)} in ${mdFiles} files${delta(mdLines, prev?.mdLines)}`);

console.log('╠═ BY LANGUAGE ' + '─'.repeat(54));
for (const [lang, v] of [...byLang.entries()].sort((a, b) => b[1].lines - a[1].lines)) {
  console.log(`║  ${pad(lang, 20)} ${num(v.lines)}  ${pad(pct(v.lines), 7)} ${String(v.files).padStart(4)} files`);
}

console.log('╠═ LARGEST FILES ' + '─'.repeat(52));
for (const f of [...source].sort((a, b) => b.lines - a.lines).slice(0, 12)) {
  console.log(`║  ${num(f.lines)}  ${f.path}`);
}

console.log('╠═ TESTS ' + '─'.repeat(60));
console.log(`║  Test files         ${num(testFiles.length)}${delta(testFiles.length, prev?.testFiles)}`);
console.log(`║  Test cases (approx)${num(testCases)}${delta(testCases, prev?.testCases)}`);
console.log(`║  Lines of test      ${num(testLines)}  ${pct(testLines)} of all code`);
const nonTest = source.length - testFiles.length;
console.log(`║  Coverage by file    ${String((testFiles.length / Math.max(1, nonTest) * 100).toFixed(0)).padStart(8)}%  of non-test source files have a .test beside them`);
console.log('╚' + '═'.repeat(68) + '\n');

writeFileSync(SNAPSHOT, JSON.stringify({
  at: new Date().toISOString().slice(0, 16).replace('T', ' '),
  codeLines, sourceFiles: source.length, mdLines, mdFiles,
  testFiles: testFiles.length, testCases, testLines,
}, null, 1));
