// Class declarations with methods and fields.
class Point {
  x = 0;
  y = 0;

  move(dx, dy) {
    this.x += dx;
    this.y += dy;
    return this;
  }

  origin() {
    return this.x === 0 && this.y === 0;
  }
}

class Named {
  name() {
    return this.constructor.name;
  }
}
