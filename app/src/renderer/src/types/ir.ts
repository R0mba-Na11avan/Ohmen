/* AUTO-GENERATED from the Design pydantic JSON-Schema. Do not edit. */

export type Ref = string;
export type Value = string | null;
export type Footprint = string | null;
export type Datasheet = string | null;
export type Mpn = string | null;
export type InBom = boolean;
export type Dnp = boolean;
export type Components = Component[];
export type Name = string;
/**
 * Component reference designator, e.g. 'U1'.
 */
export type Ref1 = string;
/**
 * Pin number/name as the EDA tool reports it.
 */
export type Pin = string;
/**
 * Human pin name if known, e.g. 'EN', 'VIN'.
 */
export type Pinfunction = string | null;
/**
 * Electrical type from the symbol: input, output, bidirectional, power_in, power_out, passive, no_connect, ...
 */
export type Pintype = string | null;
export type Nodes = Node[];
export type Nets = Net[];

/**
 * A whole design: parts + connectivity, tool-neutral.
 *
 * `meta` records provenance (source tool, version, source file) without
 * constraining the schema.
 */
export interface Design {
  components?: Components;
  nets?: Nets;
  meta?: Meta;
  [k: string]: unknown;
}
/**
 * A placed part and its fields.
 *
 * `fields` absorbs arbitrary extra schematic fields (rating, tolerance, ...)
 * so new metadata never forces a schema change. `mpn` is reserved: the KiCad
 * schematic carries none (it lives in the BOM), but the IR models it because
 * other adapters and Loop A populate it.
 */
export interface Component {
  ref: Ref;
  value?: Value;
  footprint?: Footprint;
  datasheet?: Datasheet;
  mpn?: Mpn;
  fields?: Fields;
  in_bom?: InBom;
  dnp?: Dnp;
  [k: string]: unknown;
}
export interface Fields {
  [k: string]: string;
}
/**
 * A net: a named electrical connection and every pin on it.
 */
export interface Net {
  name: Name;
  nodes?: Nodes;
  [k: string]: unknown;
}
/**
 * One pin of one component, as it attaches to a net.
 */
export interface Node {
  ref: Ref1;
  pin: Pin;
  pinfunction?: Pinfunction;
  pintype?: Pintype;
  [k: string]: unknown;
}
export interface Meta {
  [k: string]: string;
}
