// Generics: functions, classes and constraints.
function identity<T>(value: T): T {
  return value;
}

class Box<T extends object> {
  constructor(private item: T) {}

  unwrap(): T {
    return this.item;
  }
}

function first<T>(items: readonly T[]): T | undefined {
  return items[0];
}

type Pair<A, B> = [A, B];
