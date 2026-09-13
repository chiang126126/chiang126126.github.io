// Test du connecteur Awin sur un flux d'exemple (sans réseau ni identifiants).
// Usage : tsx scripts/test-awin.ts
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// Les EAN suivis sont lus depuis l'environnement par config.ts (au moment de l'import).
// On les définit AVANT l'import dynamique du connecteur pour tester l'appariement par EAN.
process.env.EAN_PORTASPLIT_12000 = '6973847230012,6973847230029';
const { mapFeed, parseCsv, rowToStatus, matchProduct } = await import('../src/connectors/awinFeed.ts');

const __dirname = dirname(fileURLToPath(import.meta.url));
const csv = readFileSync(join(__dirname, 'fixtures/awin-manomano-sample.csv'), 'utf8');

let failures = 0;
function assert(label: string, cond: boolean) {
  console.log(`${cond ? '✓' : '✗'} ${label}`);
  if (!cond) failures++;
}

// 1) Parsing : 5 lignes (en-tête + 4), champs avec virgule entre guillemets gérés.
const rows = parseCsv(csv);
assert('parseCsv : 5 lignes (header + 4)', rows.length === 5);
assert('parseCsv : virgule dans un champ quoté préservée (COMFEE 2,6kW)',
  rows[2].some((c) => c.includes('2,6kW')));

// 2) Statut.
assert('rowToStatus : in_stock=1 qty=12 → in_stock', rowToStatus('1', '12').status === 'in_stock');
assert('rowToStatus : in_stock=1 qty=2 → low_stock', rowToStatus('1', '2').status === 'low_stock');
assert('rowToStatus : in_stock=0 → out_of_stock', rowToStatus('0', '0').status === 'out_of_stock');

// 3) Appariement produit (EAN puis mots-clés).
assert('matchProduct : EAN PortaSplit', matchProduct('peu importe', '6973847230012') === 'midea-portasplit-12000');
assert('matchProduct : mots-clés COMFEE 9000', matchProduct("COMFEE' mobile 9000 BTU", '') === 'comfee-9000');
assert('matchProduct : ventilateur non suivi → null', matchProduct('Rowenta Ventilateur colonne', '3121040075123') === null);

// 4) Mapping complet → InventoryRecord rattachés au magasin online 'manomano-fr'.
const recs = mapFeed(csv, 'manomano-fr');
assert('mapFeed : 3 produits suivis retenus (ventilateur exclu)', recs.length === 3);
assert('mapFeed : storeId = manomano-fr', recs.every((r) => r.storeId === 'manomano-fr'));
assert('mapFeed : source = affiliate', recs.every((r) => r.source === 'affiliate'));
assert('mapFeed : PortaSplit en stock à 799€',
  recs.some((r) => r.productId === 'midea-portasplit-12000' && r.status === 'in_stock' && r.price === 799));
assert('mapFeed : PortaSplit "pack hiver" en rupture',
  recs.filter((r) => r.productId === 'midea-portasplit-12000').some((r) => r.status === 'out_of_stock'));
assert('mapFeed : COMFEE low_stock (qty 2)',
  recs.some((r) => r.productId === 'comfee-9000' && r.status === 'low_stock' && r.qty === 2));

console.log(failures === 0 ? '\n✅ Tous les tests passent.' : `\n❌ ${failures} test(s) en échec.`);
process.exit(failures === 0 ? 0 : 1);
