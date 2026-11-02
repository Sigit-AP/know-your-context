// Import forms including type-only imports.
import fs from "fs";
import { readFileSync } from "fs";
import type { Stats } from "fs";
import * as path from "path";
import { helper } from "./mod";

export function load(name: string): Stats | null {
  helper(path.sep);
  return fs.statSync(name, { throwIfNoEntry: false }) ?? null;
}
