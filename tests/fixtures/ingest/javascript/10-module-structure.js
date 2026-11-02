// Exports, constants and module-level state.
export const VERSION = "1.2.3";
export const MAX_RETRIES = 3;

let counter = 0;

export function bump() {
  counter += 1;
  return counter;
}

export default class Registry {
  size() {
    return counter;
  }
}
