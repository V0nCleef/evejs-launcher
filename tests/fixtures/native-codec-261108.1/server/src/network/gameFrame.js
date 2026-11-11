"use strict";

// Build3396210 SocketGPS installs MaxPacketSize=10485760. Its default
// CarbonIO backend counts the four-byte prefix in both send and receive limits.
// This is separate from MachoNet's configurable outgoing message threshold.
const GAME_FRAME_HEADER_BYTES = 4;
const MAX_GAME_FRAME_BYTES = 10 * 1024 * 1024;
const MAX_GAME_PAYLOAD_BYTES = MAX_GAME_FRAME_BYTES - GAME_FRAME_HEADER_BYTES;

function assertGamePayloadLength(length) {
  if (!Number.isSafeInteger(length) || length <= 0 || length > MAX_GAME_PAYLOAD_BYTES) {
    const error = new RangeError(`invalid game payload length: ${length}`);
    error.code = "GAME_FRAME_LENGTH_INVALID";
    throw error;
  }
}

function frameGamePayload(payload) {
  assertGamePayloadLength(payload.length);
  const header = Buffer.alloc(GAME_FRAME_HEADER_BYTES);
  header.writeUInt32LE(payload.length, 0);
  return Buffer.concat([header, payload]);
}

module.exports = {
  GAME_FRAME_HEADER_BYTES,
  MAX_GAME_FRAME_BYTES,
  MAX_GAME_PAYLOAD_BYTES,
  assertGamePayloadLength,
  frameGamePayload,
};
