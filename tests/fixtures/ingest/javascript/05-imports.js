// ES module import forms.
import fs from "fs";
import { readFileSync } from "fs";
import * as path from "path";
import { helper as localHelper } from "./mod.js";

export function useThem() {
  return [fs, readFileSync, path, localHelper];
}
