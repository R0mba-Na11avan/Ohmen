/* AUTO-GENERATED from the BomRow pydantic JSON-Schema. Do not edit. */

export type Id = string;
export type Refdes = string[];
export type Qty = number;
export type Value = string | null;
export type Footprint = string | null;
export type Mpn = string | null;
export type DatasheetUrl = string | null;
export type ProductUrl = string | null;
export type Why = string;
export type UnitPrice = number | null;
export type Manufacturer = string | null;
export type DistributorPn = string | null;
export type Stock = number | null;
export type Lifecycle = string | null;
export type FetchedAt = string | null;
export type MinOrder = number | null;
export type Source = "schematic" | "llm" | "manual";
export type Status = "active" | "pinned" | "rejected";
export type DuplicateOf = string[];
export type SeparateFrom = string[];
/**
 * What the line costs to order: qty × unit_price, or the whole minimum lot
 * when the part isn't sold in fewer (a reel of 3000 costs the reel). None
 * when no price is set; rejected rows count 0.
 */
export type LineTotal = number | null;

/**
 * One line of the bill of materials.
 *
 * A schematic-sourced row groups every placed part that shares a (value,
 * footprint) — so `refdes` is a list and `qty` its length. LLM/manual rows are
 * planned parts: once the schematic places one, a fill merges it into the
 * schematic row (see bom/match.py). `id` is stable for schematic rows (derived from
 * value+footprint) so a re-fill updates in place instead of duplicating.
 */
export interface BomRow {
  id: Id;
  refdes?: Refdes;
  qty?: Qty;
  value?: Value;
  footprint?: Footprint;
  mpn?: Mpn;
  datasheet_url?: DatasheetUrl;
  product_url?: ProductUrl;
  why?: Why;
  unit_price?: UnitPrice;
  manufacturer?: Manufacturer;
  distributor_pn?: DistributorPn;
  stock?: Stock;
  lifecycle?: Lifecycle;
  fetched_at?: FetchedAt;
  min_order?: MinOrder;
  source?: Source;
  status?: Status;
  duplicate_of?: DuplicateOf;
  separate_from?: SeparateFrom;
  line_total: LineTotal;
  [k: string]: unknown;
}
