"""Fixed, read-only Docker projection for current character display values."""

# No shell, server imports, writes, account secrets or wallet histories. The
# provider validates the exact argv and the returned projection separately.
DISPLAY_SCRIPT = r'''
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
  const hasWallet = !!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='walletAuthorityState'").get();
  let sql = "SELECT c.key AS id, json_extract(c.json,'$.balance') AS balance, json_extract(c.json,'$.securityStatus') AS securityStatus, json_extract(c.json,'$.securityRating') AS securityRating, json_extract(c.json,'$.solarSystemID') AS systemID";
  if (hasWallet) sql += ", json_extract(w.json,'$.balance') AS authoritativeBalance";
  sql += ' FROM characters c';
  if (hasWallet) sql += " LEFT JOIN walletAuthorityState w ON w.key = 'character:' || c.key";
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
    const balance = finite(row.authoritativeBalance) ? row.authoritativeBalance : row.balance;
    if (finite(balance)) result.balance = balance;
    const security = finite(row.securityStatus) ? row.securityStatus : row.securityRating;
    if (finite(security)) result.securityStatus = security;
    if (system) {
      result.solarSystemName = system.solarSystemName;
      const sec = system.securityStatus ?? system.security;
      if (finite(sec)) result.solarSystemSecurity = sec;
    }
    return result;
  });
  process.stdout.write(JSON.stringify({players}));
} finally { db.close(); }
'''.strip()
