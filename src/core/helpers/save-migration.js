"use strict";

// Runs with Node from the installation or from its existing Docker image.
// "bundle" is a launcher-owned directory. Live paths arrive as resolved argv
// data and are never interpolated into a shell command.
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

const names = new Set(["game", "game-wal", "game-shm", "game-journal",
  "market", "market-wal", "market-shm", "market-journal", "images", "skins"]);
const block = Buffer.alloc(1024 * 1024);
const renamePause = new Int32Array(new SharedArrayBuffer(4));

function stat(file) {
  try {
    return fs.lstatSync(file);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function plain(file) {
  if (!path.isAbsolute(file) || path.normalize(file) !== file) throw Error("Invalid save path.");
  for (let current = file; ; current = path.dirname(current)) {
    const info = stat(current);
    if (info && info.isSymbolicLink()) throw Error("Save paths cannot contain links.");
    if (current === path.dirname(current)) break;
  }
}

function hashTree(file, hash, relative = "") {
  const info = stat(file);
  if (!info) throw Error("A save file disappeared while it was being verified.");
  if (info.isSymbolicLink()) throw Error("A save contains a symbolic link or junction.");
  if (info.isDirectory()) {
    hash.update(JSON.stringify([relative, "directory"]));
    for (const child of fs.readdirSync(file).sort()) {
      hashTree(path.join(file, child), hash, relative + "/" + child);
    }
    return;
  }
  if (!info.isFile() || info.nlink > 1) throw Error("A save contains a special or hard-linked file.");
  hash.update(JSON.stringify([relative, "file", info.size]));
  const fd = fs.openSync(file, "r");
  try {
    for (let size; (size = fs.readSync(fd, block, 0, block.length, null)); ) {
      hash.update(block.subarray(0, size));
    }
  } finally {
    fs.closeSync(fd);
  }
}

function fingerprint(file) {
  if (!stat(file)) return null;
  const hash = createHash("sha256");
  hashTree(file, hash);
  return hash.digest("hex");
}

function inventory(entries) {
  return Object.fromEntries(entries.map(([name, file]) => [name, fingerprint(file)]));
}

function same(left, right) {
  return Object.keys(left).length === Object.keys(right).length
    && Object.entries(left).every(([name, hash]) => right[name] === hash);
}

function copyTree(from, to, owner) {
  const info = stat(from);
  if (!info || info.isSymbolicLink() || (!info.isDirectory() && (!info.isFile() || info.nlink > 1))) {
    throw Error("A save member changed while being copied.");
  }
  if (info.isDirectory()) {
    fs.mkdirSync(to);
    for (const child of fs.readdirSync(from)) copyTree(path.join(from, child), path.join(to, child), owner);
  } else {
    fs.copyFileSync(from, to, fs.constants.COPYFILE_EXCL);
    const fd = fs.openSync(to, "r+");
    try {
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
  }
  if (owner) fs.chownSync(to, owner[0], owner[1]);
}

function capture(entries, bundle, hostOwner) {
  const before = inventory(entries);
  for (const [name, file] of entries) {
    if (before[name]) copyTree(file, path.join(bundle, name), hostOwner);
  }
  const copied = inventory(entries.map(([name]) => [name, path.join(bundle, name)]));
  if (!same(before, copied) || !same(before, inventory(entries))) {
    throw Error("A save changed while being copied. Keep Game and Market stopped and retry.");
  }
  return before;
}

function renameSaveMember(from, to) {
  for (let retry = 0; ; retry++) {
    try {
      fs.renameSync(from, to);
      return;
    } catch (error) {
      if (process.platform !== "win32" || error.code !== "EPERM" || retry === 3) throw error;
      // Give brief Windows sharing conflicts time to clear inside the transaction.
      Atomics.wait(renamePause, 0, 0, 50 * 2 ** retry);
    }
  }
}

function replace(entries, bundle, expected, incoming, keep, docker) {
  if (!same(inventory(entries), expected)) throw Error("The save changed since its backup. Review it again.");
  const changes = entries.filter(([name]) => expected[name] !== incoming[name] && !keep.includes(name));
  const moves = [];
  let rollbackFailed = false;
  try {
    for (const [name, live] of changes) {
      const parent = path.dirname(live);
      let authority = live;
      while (!stat(authority)) authority = path.dirname(authority);
      const info = stat(authority);
      const owner = docker ? [info.uid, info.gid] : null;
      if (incoming[name]) {
        const created = fs.mkdirSync(parent, { recursive: true });
        if (created && owner) {
          for (let folder = parent; ; folder = path.dirname(folder)) {
            fs.chownSync(folder, owner[0], owner[1]);
            if (folder === created) break;
          }
        }
      }
      if (!stat(parent)) continue;
      // mkdtemp needs an extended-length prefix for deeply nested Windows paths.
      const prefix = path.join(parent, ".evejs-save-migration-");
      const created = fs.mkdtempSync(path.toNamespacedPath(prefix));
      const stage = path.join(parent, path.basename(created));
      const move = { name, live, stage, old: path.join(stage, "original"),
        next: path.join(stage, "incoming"), displaced: false, installed: false };
      moves.push(move);
      if (incoming[name]) {
        const from = path.join(bundle, name);
        plain(from);
        if (fingerprint(from) !== incoming[name]) throw Error("The prepared copy changed.");
        copyTree(from, move.next, owner);
        if (fingerprint(move.next) !== incoming[name]) throw Error("Could not verify the staged copy.");
      }
    }
    if (!same(inventory(entries), expected)) throw Error("The save changed while preparing the transfer.");
    for (const move of moves) {
      if (expected[move.name]) {
        renameSaveMember(move.live, move.old);
        move.displaced = true;
      }
      if (incoming[move.name]) {
        renameSaveMember(move.next, move.live);
        move.installed = true;
      }
    }
    if (!same(inventory(entries), incoming)) throw Error("Could not verify the published save.");
  } catch (error) {
    for (const move of moves.reverse()) {
      try {
        if (move.installed) renameSaveMember(move.live, move.next);
        if (move.displaced) renameSaveMember(move.old, move.live);
      } catch (_) {
        rollbackFailed = true;
      }
    }
    if (rollbackFailed) throw Error("Could not roll back the save. Keep both runtimes stopped and use the backups.");
    throw error;
  } finally {
    // Windows and Docker may briefly lock a displaced directory. Any recovery
    // copies still on disk remain in their transaction-owned sibling folders.
    if (!rollbackFailed) {
      for (const move of moves) {
        try {
          fs.rmSync(move.stage, { recursive: true });
        } catch (_) {}
      }
    }
  }
  return incoming;
}

try {
  const request = JSON.parse(fs.readFileSync(0, "utf8"));
  const { operation, files, bundle, expected, incoming, keep = [], hostOwner, docker } = request;
  const entries = Object.entries(files);
  if (!entries.length || entries.some(([name]) => !names.has(name))) throw Error("Unexpected save member.");
  for (const [, file] of entries) plain(file);
  const data = path.join(path.dirname(files.game), "data");
  plain(data);
  if (!stat(data)?.isDirectory()) throw Error("Set up GameStore in both installations before transferring a save.");
  if (bundle) plain(bundle);
  let result;
  if (operation === "inventory") result = inventory(entries);
  else if (operation === "capture") result = capture(entries, bundle, hostOwner);
  else if (operation === "replace") result = replace(entries, bundle, expected, incoming, keep, docker);
  else throw Error("Unsupported transfer action.");
  process.stdout.write(JSON.stringify({ ok: true, files: result }));
} catch (error) {
  process.stdout.write(JSON.stringify({ ok: false, message: String(error.message).slice(0, 300) }));
  process.exitCode = 1;
}
