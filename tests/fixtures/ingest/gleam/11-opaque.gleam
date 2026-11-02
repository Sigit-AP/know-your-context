// Opaque types hiding internals.
pub opaque type Counter {
  Counter(value: Int)
}

pub fn new_counter() -> Counter {
  Counter(0)
}

pub fn bump(counter: Counter) -> Counter {
  Counter(counter.value + 1)
}

pub fn value(counter: Counter) -> Int {
  counter.value
}
