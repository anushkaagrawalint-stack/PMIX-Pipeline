import { readFileSync } from 'fs';
import { neon } from '/Users/admin/Downloads/pmix-dashboard/node_modules/@neondatabase/serverless/index.mjs';
const env = readFileSync('/Users/admin/Downloads/pmix-dashboard/.env', 'utf8');
const sql = neon(env.match(/DATABASE_URL=(.+)/)[1].trim());

const rows = readFileSync('/private/tmp/claude-501/-Users-admin-Downloads-pmix-dashboard/082464bc-524e-4036-817a-e43c0647b4fd/scratchpad/flipped28.csv','utf8')
  .trim().split('\n').slice(1).map(l => {
    const [sel, key, loc, date, name, mOld, mNew] = l.split(',');
    return { sel, loc, date: date.slice(0,10), name, mOld };
  });

// 1. sale-time itemGroup guid per selection, from raw
const sels = rows.map(r => r.sel);
const raws = await sql`
  SELECT sel.value->>'guid' AS sel_guid, sel.value->'itemGroup'->>'guid' AS group_guid
  FROM raw.toast_orders o,
  LATERAL jsonb_array_elements(o.payload->'checks') chk(value),
  LATERAL jsonb_array_elements(chk.value->'selections') sel(value)
  WHERE sel.value->>'guid' = ANY(${sels})`;
const selToGroup = new Map(raws.map(r => [r.sel_guid, r.group_guid]));
console.log('selections found in raw:', raws.length, '/', sels.length);

// 2. as-of snapshot per (loc, date): walk groups building group_guid -> {menu, top group}
const snapCache = new Map();
function walkGroups(groups, mName, top, sink) {
  for (const g of groups || []) {
    const effTop = top || g.name;
    if (g.guid) sink.set(g.guid, { menu: mName, group: effTop });
    walkGroups(g.menuGroups || g.subgroups || [], mName, effTop, sink);
  }
}
for (const p of [...new Set(rows.map(r => r.loc + '|' + r.date))]) {
  const [loc, date] = p.split('|');
  const snap = await sql`
    SELECT payload FROM raw.toast_config
    WHERE location_code = ${loc} AND config_type = 'menus' AND fetched_at::DATE <= ${date}::DATE
    ORDER BY fetched_at DESC LIMIT 1`;
  const menus = Array.isArray(snap[0]?.payload) ? snap[0].payload : (snap[0]?.payload?.menus || []);
  const sink = new Map();
  for (const m of menus) walkGroups(m.menuGroups, m.name, null, sink);
  snapCache.set(p, sink);
}

let ok = 0;
for (const r of rows) {
  const gg = selToGroup.get(r.sel);
  const hit = snapCache.get(r.loc + '|' + r.date)?.get(gg);
  const menu = hit?.menu ?? '(group not in as-of snapshot)';
  if (menu === r.mOld) ok++;
  else console.log('MISMATCH:', r.name, r.loc, r.date, '| group', gg?.slice(0,8), '→', menu, '(group name:', hit?.group + ')', '| expected', r.mOld);
}
console.log(`\ngroup-guid as-of resolution: ${ok}/${rows.length} lines resolve to their sale-time menu (= Toast)`);
