// Classes with access modifiers and parameter properties.
class Point {
  constructor(
    public x: number = 0,
    public y: number = 0,
  ) {}

  move(dx: number, dy: number): this {
    this.x += dx;
    this.y += dy;
    return this;
  }

  origin(): boolean {
    return this.x === 0 && this.y === 0;
  }
}

abstract class Shape {
  abstract area(): number;

  describe(): string {
    return `area=${this.area()}`;
  }
}
