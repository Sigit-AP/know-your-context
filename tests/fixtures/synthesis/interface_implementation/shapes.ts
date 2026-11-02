// A class fully covering an interface's methods.
export interface Shape {
  area(): number;
}

export class Circle {
  radius = 1;

  area(): number {
    return 3.14159 * this.radius * this.radius;
  }
}
