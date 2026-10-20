/* AUTO-GENERATED from the BomProposal pydantic JSON-Schema. Do not edit. */

export type Id = string;
export type Refdes = string[];
export type Qty = number;
export type Value = string | null;
export type Footprint = string | null;
export type CurrentMpn = string | null;
export type CurrentPrice = number | null;
export type Mpn = string;
export type Manufacturer = string;
export type Description = string;
export type DigikeyPn = string | null;
export type Packaging = string;
export type UnitPrice = number | null;
export type Stock = number | null;
export type Lifecycle = string | null;
export type ProductUrl = string | null;
export type DatasheetUrl = string | null;
export type MinOrder = number | null;
export type MinOrderCost = number | null;
export type BulkOnly = boolean;
export type PhotoUrl = string | null;
export type Category = string;
export type Alternatives = Offer[];
export type Rationale = string;
export type Status = "pending" | "approved" | "rejected" | "skipped";
export type CreatedAt = string;

export interface BomProposal {
  id: Id;
  refdes?: Refdes;
  qty?: Qty;
  value?: Value;
  footprint?: Footprint;
  current_mpn?: CurrentMpn;
  current_price?: CurrentPrice;
  offer?: Offer | null;
  alternatives?: Alternatives;
  rationale?: Rationale;
  status?: Status;
  created_at?: CreatedAt;
  [k: string]: unknown;
}
/**
 * One orderable part DigiKey has.
 */
export interface Offer {
  mpn: Mpn;
  manufacturer?: Manufacturer;
  description?: Description;
  digikey_pn?: DigikeyPn;
  packaging?: Packaging;
  unit_price?: UnitPrice;
  stock?: Stock;
  lifecycle?: Lifecycle;
  product_url?: ProductUrl;
  datasheet_url?: DatasheetUrl;
  min_order?: MinOrder;
  min_order_cost?: MinOrderCost;
  bulk_only?: BulkOnly;
  photo_url?: PhotoUrl;
  category?: Category;
  parameters?: Parameters;
  [k: string]: unknown;
}
export interface Parameters {
  [k: string]: string;
}
