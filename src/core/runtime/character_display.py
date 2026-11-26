"""Fixed, read-only Docker projection for current character display values."""

WALLET_LOOKUP_SCRIPT = r'''
function walletBalanceQuery(db) {
  if (db.pragma('application_id', {simple:true}) === 0x45564744 &&
      db.pragma('user_version', {simple:true}) === 35) {
    return db.prepare('SELECT balance_cents / 100.0 AS balance FROM character_wallets WHERE legacy_key=?');
  }
  if (db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='walletAuthorityState'").get()) {
    return db.prepare("SELECT json_extract(json,'$.balance') AS balance FROM walletAuthorityState WHERE key=?");
  }
  return null;
}
'''

# No shell, server imports, writes, account secrets or wallet histories. The
# provider validates the exact argv and the returned projection separately.
DISPLAY_SCRIPT = (WALLET_LOOKUP_SCRIPT + r'''
const fs = require('node:fs');
const Database = require('/app/server/node_modules/better-sqlite3');
const root = '/var/lib/evejs/gameStore';
const id = process.argv[1];
if (id !== undefined && !/^[1-9][0-9]*$/.test(id)) throw Error('Invalid character ID');
const db = new Database(root + '/gamestore.sqlite', {readonly:true, fileMustExist:true});
const finite = x => typeof x === 'number' && Number.isFinite(x);
try {
  db.pragma('query_only = ON');
  db.exec('BEGIN');
  const wallet = walletBalanceQuery(db);
  let sql = "SELECT c.key AS id, json_extract(c.json,'$.balance') AS balance, json_extract(c.json,'$.securityStatus') AS securityStatus, json_extract(c.json,'$.securityRating') AS securityRating, json_extract(c.json,'$.solarSystemID') AS systemID";
  if (id !== undefined) {
    sql += ", json_extract(c.json,'$.shipName') AS shipName, json_extract(c.json,'$.shipTypeID') AS shipTypeID, json_extract(c.json,'$.skillPoints') AS skillPoints";
  }
  sql += ' FROM characters c';
  if (id !== undefined) sql += ' WHERE c.key = ?';
  sql += ' LIMIT 10001';
  const rows = id === undefined ? db.prepare(sql).all() : db.prepare(sql).all(id);
  if (rows.length > 10000) throw Error('Too many characters');
  let systems = [];
  try { systems = JSON.parse(fs.readFileSync(root + '/data/solarSystems/data.json','utf8')).solarSystems || []; }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const names = new Map(systems.map(s => [Number(s.solarSystemID),s]));
  const players = rows.map(row => {
    const system = names.get(Number(row.systemID));
    const result = {characterId:row.id};
    const authoritativeBalance = wallet?.get('character:' + row.id)?.balance;
    const balance = finite(authoritativeBalance) ? authoritativeBalance : row.balance;
    if (finite(balance)) result.balance = balance;
    const security = finite(row.securityStatus) ? row.securityStatus : row.securityRating;
    if (finite(security)) result.securityStatus = security;
    if (typeof row.shipName === 'string') result.shipName = row.shipName;
    if (Number.isSafeInteger(row.shipTypeID) && row.shipTypeID >= 0) result.shipTypeID = row.shipTypeID;
    if (finite(row.skillPoints)) result.skillPoints = row.skillPoints;
    if (system) {
      result.solarSystemName = system.solarSystemName;
      const sec = system.securityStatus ?? system.security;
      if (finite(sec)) result.solarSystemSecurity = sec;
    }
    return result;
  });
  process.stdout.write(JSON.stringify({players}));
} finally { db.close(); }
''').strip()
