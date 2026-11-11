"""Fixed read-only roster projection, independent of Config Editor detail building."""

ROSTER_SCRIPT = r'''
const Database = require('/app/server/node_modules/better-sqlite3');
const db = new Database('/var/lib/evejs/gameStore/gamestore.sqlite', {readonly:true,fileMustExist:true});
try {
  db.pragma('query_only = ON');
  db.exec('BEGIN');
  const hasTable = name => !!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name);
  const wallet = hasTable('walletAuthorityState') ? db.prepare("SELECT json_extract(json,'$.balance') AS balance FROM walletAuthorityState WHERE key=?") : null;
  const systems = hasTable('solarSystems') ? db.prepare('SELECT json FROM solarSystems WHERE key=?') : null;
  function systemName(c) {
    if (typeof c.solarSystemName === 'string' && c.solarSystemName) return c.solarSystemName;
    const row = systems?.get(String(c.solarSystemID));
    if (row) { const value = JSON.parse(row.json); if (typeof value.solarSystemName === 'string') return value.solarSystemName; }
    return c.solarSystemID ? String(c.solarSystemID) : '';
  }
  let gmMask = 0n;
  try { gmMask = require('/app/server/src/services/account/accountRoleProfiles').MAX_ACCOUNT_ROLE; } catch (_) {}
  const accountRows = db.prepare('SELECT key, json FROM accounts LIMIT 10001').all();
  const characterRows = db.prepare('SELECT key, json FROM characters LIMIT 10001').all();
  if (accountRows.length > 10000 || characterRows.length > 10000) throw Error('Roster exceeds safe limit');
  const accounts = [], byId = new Map();
  for (const row of accountRows) {
    const a = JSON.parse(row.json);
    if (!Number.isSafeInteger(a.id) || a.id < 0 || typeof row.key !== 'string' || !row.key) throw Error('Invalid account identity');
    if (byId.has(String(a.id))) throw Error('Duplicate account identity');
    const role = String(a.role ?? '0');
    let staff = typeof a.isGM === 'boolean' ? a.isGM : role === 'gm';
    if (typeof a.isGM !== 'boolean') { try { staff ||= (BigInt(role) & BigInt(gmMask)) !== 0n; } catch (_) {} }
    const account = {accountId:a.id, accountName:row.key, role, banned:a.banned === true, isGM:staff};
    accounts.push(account); byId.set(String(a.id), account);
  }
  const players = []; let skippedRecords = 0;
  for (const row of characterRows) {
    const c = JSON.parse(row.json);
    const a = c.accountId == null ? undefined : byId.get(String(c.accountId));
    if (!a) { skippedRecords++; continue; }
    const personal = wallet?.get('character:' + row.key)?.balance;
    players.push({characterId:row.key,characterName:c.characterName, ...a,
      balance:typeof personal === 'number' && Number.isFinite(personal) ? personal : c.balance ?? 0, skillPoints:c.skillPoints ?? 0,
      shipName:c.shipName ?? '',shipTypeID:c.shipTypeID ?? 0,
      securityStatus:c.securityStatus ?? c.securityRating ?? 0,
      solarSystemName:systemName(c)});
  }
  process.stdout.write(JSON.stringify({protocol:'evejs-roster-v1',accounts,players,skippedRecords}));
} finally { db.close(); }
'''.strip()
