/* AUTO-GENERATED from the PartsReport pydantic JSON-Schema. Do not edit. */

export type Requirements = string;
export type Model = string;
export type Mpn = string;
export type Category = string;
export type Why = string;
export type DatasheetUrl = string | null;
export type DatasheetOk = boolean;
export type GroundedReview = string;
export type Note = string;
export type Results = PartResult[];

export interface PartsReport {
  requirements: Requirements;
  model: Model;
  results?: Results;
  [k: string]: unknown;
}
export interface PartResult {
  candidate: Candidate;
  datasheet_ok: DatasheetOk;
  grounded_review?: GroundedReview;
  note?: Note;
  [k: string]: unknown;
}
export interface Candidate {
  mpn: Mpn;
  category?: Category;
  why?: Why;
  datasheet_url?: DatasheetUrl;
  [k: string]: unknown;
}
