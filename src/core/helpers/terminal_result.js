"use strict";

const fs = require("fs");

const RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT=";
let terminalResultWritten = false;

function encodeTerminalResult(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new TypeError("terminal result must be an object");
  }
  return Buffer.from(`${RESULT_PREFIX}${JSON.stringify(payload)}\n`, "utf8");
}

function writeAllSync(fd, buffer, fsImpl = fs) {
  let offset = 0;
  while (offset < buffer.length) {
    const written = fsImpl.writeSync(
      fd,
      buffer,
      offset,
      buffer.length - offset,
    );
    if (!Number.isInteger(written) || written <= 0) {
      throw new Error("terminal result write did not make progress");
    }
    offset += written;
  }
}

function writeTerminalResultSync(payload, options = {}) {
  if (terminalResultWritten) {
    throw new Error("terminal result was already written");
  }
  terminalResultWritten = true;
  const fd = Number.isInteger(options.fd) ? options.fd : 1;
  writeAllSync(fd, encodeTerminalResult(payload), options.fsImpl || fs);
}

function exitWithTerminalResult(payload, exitCode) {
  const normalizedExitCode = Number(exitCode) === 0 ? 0 : 1;
  try {
    // stdout is normally a pipe for both the Native launcher and `compose run`.
    // A synchronous complete write is the terminal-result flush boundary. Exit
    // immediately afterward so EveJS module-load timers cannot reacquire the
    // persistence lease that GameStore has just released.
    writeTerminalResultSync(payload);
  } catch (_error) {
    process.exit(1);
    return;
  }
  process.exit(normalizedExitCode);
}

function resetForTests() {
  terminalResultWritten = false;
}

module.exports = {
  RESULT_PREFIX,
  encodeTerminalResult,
  exitWithTerminalResult,
  writeTerminalResultSync,
  _resetForTests: resetForTests,
};
