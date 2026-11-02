// Const enums, computed members and exported constants.
export const enum Flag {
  None = 0,
  Read = 1,
  Write = 2,
}

export enum Level {
  Low = 10,
  High = 20,
}

export const LIMIT = 100;

export function allowed(flag: Flag): boolean {
  return flag !== Flag.None;
}
