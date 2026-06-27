// Accès PostgreSQL via un pool partagé.
import pg from 'pg';
import { config } from './config.js';

export const pool = new pg.Pool(
  config.databaseUrl ? { connectionString: config.databaseUrl } : {}
);

export async function query<T = any>(text: string, params: any[] = []): Promise<T[]> {
  const res = await pool.query(text, params);
  return res.rows as T[];
}

export async function one<T = any>(text: string, params: any[] = []): Promise<T | null> {
  const rows = await query<T>(text, params);
  return rows[0] ?? null;
}

/** true si la base est joignable (pour /healthz et le mode dégradé). */
export async function dbReady(): Promise<boolean> {
  if (!config.databaseUrl) return false;
  try {
    await pool.query('select 1');
    return true;
  } catch {
    return false;
  }
}
