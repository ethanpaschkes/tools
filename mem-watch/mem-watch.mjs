#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────────
// WATCH THIS MACHINE'S MEMORY WHILE A LONG JOB RUNS.
//
//   node mem-watch.mjs                  # watch, warn at 5 GB
//   node mem-watch.mjs --limit 3        # warn at 3 GB instead
//   node mem-watch.mjs --every 2        # sample every 2 s (default 5)
//   node mem-watch.mjs --log mem.log    # also append to a file
//   node mem-watch.mjs --once           # print one sample and exit
//
// WHY: a big job (a 12 GB lint heap, a render, a build) on a 16 GB Mac with a browser open can push the
// machine into swap until everything stalls. This shows it happening, per application, instead of guessing
// afterwards.
//
// ⚠️ IT NEVER KILLS ANYTHING, AND THAT IS DELIBERATE. There is no safe
// automatic "free up RAM" on macOS: the only way to reclaim memory is to stop a
// process, and stopping the wrong one costs you unsaved work — a browser's
// tabs, an editor's buffer, or the agent session that is doing the job. This
// tool REPORTS. `--suspend <pid>` exists as a separate, explicit, reversible
// action you take yourself with a PID you have read. See `suspendHint`.
//
// ⚠️ NO PATTERN MATCHING ON PROCESS NAMES, ANYWHERE. `pkill -f <substring>` has
// is a classic foot-gun; a loose pattern matches the agent, the editor
// and the dev server as readily as the runaway. Everything here is by PID.
// ─────────────────────────────────────────────────────────────────────────────

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';

const argv = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
};
const LIMIT_GB = Number(flag('limit', '5'));
const EVERY_SEC = Number(flag('every', '5'));
const LOG_PATH = flag('log', null);
const ONCE = argv.includes('--once');

const GB = 1024 * 1024 * 1024;
const sh = (cmd, args) => execFileSync(cmd, args, { encoding: 'utf8' });

/** Installed physical memory, bytes. */
const TOTAL_BYTES = Number(sh('sysctl', ['-n', 'hw.memsize']).trim());

/**
 * Every process with its resident size, biggest first.
 *
 * ⚠️ RESIDENT, NOT VIRTUAL. A process's VSZ on macOS is address space it has
 * reserved and mostly never touched — Chrome's helpers routinely "show" tens of
 * gigabytes of it on a 16 GB machine, which is why the number people read off
 * Activity Monitor panics them. RSS is what is actually in RAM.
 */
function processes() {
  // ⚠️ `-m` SORTS BY MEMORY. `-r` sorts by CPU, and reading a CPU-ordered list
  // as if it were a memory one is how you conclude "nothing is using much RAM"
  // on a machine that is actively swapping.
  const out = sh('ps', ['-Ao', 'rss=,pid=,comm=', '-m']);
  return out.split('\n').filter(Boolean).map((line) => {
    const m = line.trim().match(/^(\d+)\s+(\d+)\s+(.*)$/);
    if (!m) return null;
    return { rssBytes: Number(m[1]) * 1024, pid: Number(m[2]), command: m[3] };
  }).filter(Boolean);
}

/** Swap in use, bytes. The number that actually means "the machine is hurting". */
function swapUsedBytes() {
  const m = sh('sysctl', ['-n', 'vm.swapusage']).match(/used\s*=\s*([\d.]+)([MGK])/);
  if (!m) return 0;
  const mult = m[2] === 'G' ? GB : m[2] === 'M' ? 1024 * 1024 : 1024;
  return Number(m[1]) * mult;
}

/**
 * Free percentage as macOS itself reports it.
 *
 * ⚠️ NOT `free + inactive` arithmetic off `vm_stat`. macOS counts cached file
 * pages as used and reclaims them on demand, so a hand-rolled sum reads as
 * "almost out of memory" on a machine that is perfectly comfortable.
 */
function freePercent() {
  try {
    const m = sh('memory_pressure', []).match(/free percentage:\s*(\d+)%/);
    return m ? Number(m[1]) : null;
  } catch { return null; }
}

/** A short, safe name for a long executable path. */
const shortName = (command) => {
  const app = command.match(/\/([^/]+)\.app\//);
  const base = command.split('/').pop() ?? command;
  return app && app[1] !== base ? `${app[1]} (${base})` : base;
};

/**
 * The name of the APPLICATION a process belongs to — the outermost `.app` in
 * its path, so every `Google Chrome Helper (Renderer)` rolls up under
 * `Google Chrome`.
 *
 * 🚨 THIS IS THE NUMBER PEOPLE ACTUALLY MEAN. A browser "using 20 GB" with five
 * tabs open usually has no single process anywhere near that: a modern browser is thirty-odd processes, each modest, and only the SUM
 * says whether the browser is the problem. A per-process view answers "which
 * one should I stop" and silently answers "is this app the problem" WRONG.
 */
function appOf(command) {
  const apps = [...command.matchAll(/\/([^/]+)\.app\//g)].map((m) => m[1]);
  if (apps.length > 0) return apps[0];
  if (command.startsWith('/System/') || command.startsWith('/usr/')) return 'macOS';
  return shortName(command);
}

/** Resident totals per application, biggest first. */
function byApp(procs) {
  const totals = new Map();
  for (const p of procs) {
    const name = appOf(p.command);
    const e = totals.get(name) ?? { name, rssBytes: 0, count: 0, biggest: p };
    e.rssBytes += p.rssBytes;
    e.count += 1;
    if (p.rssBytes > e.biggest.rssBytes) e.biggest = p;
    totals.set(name, e);
  }
  return [...totals.values()].sort((a, b) => b.rssBytes - a.rssBytes);
}

const fmtGB = (bytes) => `${(bytes / GB).toFixed(2)} GB`;

/**
 * Processes that must NEVER be offered as something to pause.
 *
 * 🚨 THE FIRST VERSION OF THIS TOOL OFFERED `kill -STOP 397` ON WindowServer,
 * which draws every pixel on the screen — suspending it freezes the whole
 * display with no way left to un-freeze it. A tool that suggests an action is responsible for the
 * action it suggests.
 */
const NEVER_SUSPEND = new Set([
  'WindowServer', 'launchd', 'kernel_task', 'loginwindow', 'logind',
  'runningboardd', 'coreaudiod', 'SystemUIServer', 'Dock', 'Finder',
  'opendirectoryd', 'notifyd', 'configd', 'securityd', 'hidd', 'powerd',
]);

function suspendHint(p) {
  const base = p.command.split('/').pop() ?? p.command;
  if (NEVER_SUSPEND.has(base) || p.pid <= 1) {
    return `      ⚠️ do NOT pause this one — the system needs it to keep running.`;
  }
  return `      to PAUSE it (reversible, nothing is lost):  kill -STOP ${p.pid}\n`
       + `      to let it continue:                         kill -CONT ${p.pid}`;
}

const seenOver = new Map();   // pid -> peak bytes, so one warning per escalation

function sample() {
  const procs = processes();
  const swap = swapUsedBytes();
  const free = freePercent();
  const apps = byApp(procs);
  // ⚠️ `macOS` IS NOT ACTIONABLE. It is the roll-up of several hundred system
  // daemons, it is always large, and there is nothing a person can usefully do
  // about it — warning on it is noise that trains you to ignore the warnings.
  const over = apps.filter((a) => a.name !== 'macOS' && a.rssBytes >= LIMIT_GB * GB);
  const stamp = new Date().toTimeString().slice(0, 8);

  const lines = [];
  lines.push(
    `[${stamp}] free ${free === null ? '?' : `${free}%`}`
    + ` · swap ${fmtGB(swap)}`
    + ` · ${apps.slice(0, 5).map((a) => `${a.name} ${fmtGB(a.rssBytes)}${a.count > 1 ? `×${a.count}` : ''}`).join(' · ')}`,
  );

  for (const a of over) {
    const peak = seenOver.get(a.name) ?? 0;
    // Re-warn only when it has grown by another half-gig, so a steady hog does
    // not fill the screen while a growing one still gets noticed.
    if (a.rssBytes < peak + 0.5 * GB) continue;
    seenOver.set(a.name, a.rssBytes);
    lines.push(
      `\n  🚨 OVER ${LIMIT_GB} GB — ${a.name}  ${fmtGB(a.rssBytes)} across ${a.count} process${a.count === 1 ? '' : 'es'}`
      + ` (${((a.rssBytes / TOTAL_BYTES) * 100).toFixed(0)}% of this machine)\n`
      + `      its biggest single process: ${shortName(a.biggest.command)} pid ${a.biggest.pid} ${fmtGB(a.biggest.rssBytes)}\n`
      + suspendHint(a.biggest),
    );
  }
  // Swap in use at all is the real alarm: it means the machine has already run
  // out and started writing RAM to disk, which is what makes everything crawl.
  if (swap > 0.5 * GB) {
    lines.push(`\n  🚨 SWAPPING — ${fmtGB(swap)} of memory has been pushed to disk.`
      + ` Everything will feel slow until something above is closed or paused.`);
  }

  const text = lines.join('\n');
  console.log(text);
  if (LOG_PATH) fs.appendFileSync(LOG_PATH, `${text}\n`);
}

console.log(`mem-watch · ${(TOTAL_BYTES / GB).toFixed(0)} GB installed`
  + ` · warning at ${LIMIT_GB} GB per process`
  + ` · sampling every ${EVERY_SEC}s`
  + `${LOG_PATH ? ` · logging to ${LOG_PATH}` : ''}`);
console.log('This tool never kills anything. Ctrl-C to stop.\n');

sample();
if (!ONCE) setInterval(sample, EVERY_SEC * 1000);
