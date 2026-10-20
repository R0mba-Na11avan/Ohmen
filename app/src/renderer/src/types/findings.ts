/* AUTO-GENERATED from the Finding pydantic JSON-Schema. Do not edit. */

export type Severity = "must-fix" | "verify" | "cosmetic";
export type Title = string;
export type Detail = string;
/**
 * What this prevents ('tie EN or the buck never starts').
 */
export type FailureMode = string;
/**
 * Specific spec/pattern ref; required for must-fix to survive the gate.
 */
export type Citation = string | null;
export type Source = "pattern" | "llm" | "verifier" | "erc" | "drc" | "distributor";
/**
 * For pattern-sourced findings: trusted vs AI-proposed provisional.
 */
export type PatternStatus = ("trusted" | "provisional") | null;
/**
 * Whether a connectivity claim was checked against the netlist.
 */
export type ConnectivityVerified = "verified" | "unverified" | "not-applicable";
export type ComponentRef = string | null;
export type Net = string | null;
/**
 * Stable id of the pattern check, if any.
 */
export type RuleId = string | null;
/**
 * e.g. 'unverified', 'AI-proposed'.
 */
export type Labels = string[];
/**
 * Part key of the cited datasheet.
 */
export type Datasheet = string | null;
/**
 * 1-based page of the cited datasheet.
 */
export type Page = number | null;
/**
 * Verbatim quote from that page.
 */
export type Excerpt = string | null;
export type Rank = number;
/**
 * Stable identity for muting: same issue on the same target mutes once.
 *
 * Pattern findings key on rule + component (NOT net — the net can be a
 * volatile 'unconnected-(...)' name that changes when the bug is what makes
 * it float). LLM findings, having no rule_id, fall back to a hash of
 * title + component so they are still mutable.
 */
export type MuteKey = string;

/**
 * One issue in a review.
 */
export interface Finding {
  severity: Severity;
  title: Title;
  detail?: Detail;
  failure_mode?: FailureMode;
  citation?: Citation;
  source: Source;
  pattern_status?: PatternStatus;
  connectivity_verified?: ConnectivityVerified;
  component_ref?: ComponentRef;
  net?: Net;
  rule_id?: RuleId;
  labels?: Labels;
  datasheet?: Datasheet;
  page?: Page;
  excerpt?: Excerpt;
  rank: Rank;
  mute_key: MuteKey;
  [k: string]: unknown;
}
