// Prototype-style definitions and getters.
class Queue {
  #items = [];

  get length() {
    return this.#items.length;
  }

  push(item) {
    this.#items.push(item);
    return this;
  }

  [Symbol.iterator]() {
    return this.#items[Symbol.iterator]();
  }
}

function drain(queue) {
  return [...queue];
}
