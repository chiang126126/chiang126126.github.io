// Initialise la base : applique schema.sql puis seed.sql.
// Usage : DATABASE_URL=... node --experimental-strip-types scripts/init-db.ts
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import pg from 'pg';

const __dirname = dirname(fileURLToPath(import.meta.url));
const url = process.env.DATABASE_URL;
if (!url) { console.error('DATABASE_URL manquant'); process.exit(1); }

const pool = new pg.Pool({ connectionString: url });

async function run() {
  for (const file of ['../db/schema.sql', '../db/seed.sql']) {
    const sql = readFileSync(join(__dirname, file), 'utf8');
    console.log(`Application de ${file}…`);
    await pool.query(sql);
  }
  console.log('✓ Base initialisée.');
  await pool.end();
}
run().catch((e) => { console.error(e); process.exit(1); });
