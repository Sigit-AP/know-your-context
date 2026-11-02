// Namespaces (internal modules) and nested declarations.
namespace Geometry {
  export interface PointLike {
    x: number;
    y: number;
  }

  export function distance(a: PointLike, b: PointLike): number {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }
}

namespace Strings {
  export function shout(s: string): string {
    return s.toUpperCase();
  }
}
