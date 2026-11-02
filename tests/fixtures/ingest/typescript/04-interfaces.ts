// Interfaces, method signatures and extension.
interface Shape {
  area(): number;
  name: string;
}

interface Named {
  name: string;
}

interface Circle extends Shape, Named {
  radius: number;
}

function totalArea(shapes: Shape[]): number {
  return shapes.reduce((sum, s) => sum + s.area(), 0);
}
