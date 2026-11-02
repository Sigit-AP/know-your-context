// Decorators and accessor syntax.
function sealed(constructor: Function) {
  Object.seal(constructor.prototype);
}

@sealed
class Registry {
  private items: string[] = [];

  get size(): number {
    return this.items.length;
  }

  add(item: string): void {
    this.items.push(item);
  }
}
