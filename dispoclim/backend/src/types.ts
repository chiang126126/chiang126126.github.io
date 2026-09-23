// Types partagés — InventoryRecord est le format pivot (identique au front démo).

export type Status = 'in_stock' | 'low_stock' | 'out_of_stock';
export type Reliability = 'high' | 'medium' | 'low';
export type Source = 'api' | 'affiliate' | 'scrape';
export type Fulfillment = 'pickup' | 'delivery';

export interface InventoryRecord {
  storeId: string;
  productId: string;
  status: Status;
  qty: number | null;
  price: number | null; // EUR TTC
  fulfillment: Fulfillment[];
  updatedAt: number; // epoch ms
  reliability: Reliability;
  source: Source;
}

export interface Store {
  id: string;
  retailerId: string;
  name: string;
  city: string | null;
  cp: string | null;
  lat: number | null;
  lon: number | null;
  online: boolean;
  pollTier: 'hot' | 'warm' | 'cold';
}

export interface Product {
  id: string;
  brand: string;
  name: string;
}

export interface CollectContext {
  products: Product[];
  stores: Store[];
  userAgent: string;
  signal?: AbortSignal;
}

export interface Connector {
  retailer: string;
  source: Source;
  fetchRecords(ctx: CollectContext): Promise<InventoryRecord[]>;
}

/** Transition détectée par le collector → consommée par l'alert engine. */
export interface RestockEvent {
  storeId: string;
  productId: string;
  from: Status;
  to: Status;
  price: number | null;
  at: number;
}
